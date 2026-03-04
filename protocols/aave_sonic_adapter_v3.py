"""
AAVE Protocol Adapter V3 for Sonic network
This adapter receives aToken address and extracts underlying token address from it
"""
import json
import os
import logging
from web3 import Web3
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_token_price
from .core.cache_manager import cache_manager

def load_abi(filename):
    """
    Load ABI from a JSON file
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    abi_path = os.path.join(current_dir, 'abi', filename)
    with open(abi_path, 'r') as file:
        return json.load(file)

# Load aToken ABI for getting underlying address
AAVE_ATOKEN_ABI = load_abi('aave_atoken_abi.json')

class AaveSonicAdapterV3(BaseProtocolAdapter):
    """
    Adapter for AAVE protocol V3 on Sonic network
    This version receives aToken address and extracts underlying token address
    """
    
    SOURCE = "Aave Sonic V3"
    PROTOCOL_NAME = "Aave"
    NETWORK_NAME = "Sonic"
    RPC_URL = "https://rpc.soniclabs.com"
    AAVE_CONTRACT_ADDRESS = "0x306c124ffba5f2bc0bcaf40d249cf19d492440b9"  # Main AAVE contract from env
    _web3 = None

    @classmethod
    def get_web3_provider(cls):
        if cls._web3 is None:
            cls._web3 = Web3(Web3.HTTPProvider(cls.RPC_URL))
        return cls._web3

    @classmethod
    def get_underlying_asset_address(cls, atoken_address: str) -> str:
        cache_key = f"underlying_{atoken_address.lower()}"
        cached = cache_manager.get_cached_data('aave_v3', 'sonic', cache_key)
        if cached:
            return cached.get('underlying_address')
        try:
            w3 = cls.get_web3_provider()
            atoken_contract = w3.eth.contract(
                address=Web3.to_checksum_address(atoken_address),
                abi=AAVE_ATOKEN_ABI
            )
            try:
                underlying_address = atoken_contract.functions.UNDERLYING_ASSET_ADDRESS().call()
            except:
                underlying_address = atoken_contract.functions.asset().call()
            cache_manager.set_cached_data('aave_v3', 'sonic', cache_key, {'underlying_address': underlying_address}, ttl=None)
            return underlying_address
        except Exception as e:
            logging.error(f"Error getting underlying asset address from aToken {atoken_address}: {e}")
            return None
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from AAVE protocol V3
        """
        # Check in source field
        if fund_data.get('source') == cls.SOURCE:
            return True
        return False
    
    @classmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        """
        Calculate borrow rate for AAVE reserves using the formula from DefaultReserveInterestRateStrategyV2
        
        Args:
            utilization: Current utilization rate (0-1)
            optimal_usage_ratio: Optimal utilization rate (0-1)
            base_variable_borrow_rate: Base variable borrow rate
            variable_rate_slope1: Slope 1 for variable rate
            variable_rate_slope2: Slope 2 for variable rate
            
        Returns:
            float: Calculated borrow APY
        """
        # Start with base rate
        borrow_rate = base_variable_borrow_rate
        
        if utilization > optimal_usage_ratio:
            # If utilization is above optimal, calculate excess utilization
            excess_utilization_ratio = (utilization - optimal_usage_ratio) / (1 - optimal_usage_ratio)
            
            # Add slope1 plus slope2 multiplied by excess utilization
            borrow_rate += variable_rate_slope1 + (variable_rate_slope2 * excess_utilization_ratio)
        else:
            # If utilization is below optimal, scale slope1 by utilization ratio
            borrow_rate += variable_rate_slope1 * (utilization / optimal_usage_ratio)
        
        return borrow_rate
    
    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY for Aave reserve based on the DefaultReserveInterestRateStrategyV2 contract
        
        Args:
            our_supply: Amount we're planning to supply
            reserve_data: Reserve data dictionary
            
        Returns:
            tuple: (reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr)
        """
        total_supplied = reserve_data.get('total_supplied', 0) + our_supply
        total_borrowed = reserve_data.get('total_borrowed', 0)
        utilization = total_borrowed / total_supplied if total_supplied > 0 else 0
        
        # Get variables from reserve data
        optimal_usage_ratio = reserve_data.get('optimal_usage_ratio', 0.8)
        base_variable_borrow_rate = reserve_data.get('base_variable_borrow_rate', 0)
        variable_rate_slope1 = reserve_data.get('variable_rate_slope1', 0.08)
        variable_rate_slope2 = reserve_data.get('variable_rate_slope2', 0.8)
        reserve_factor = reserve_data.get('reserve_factor', 0)
        
        # Calculate borrow rate
        borrow_rate = cls.calculate_borrow_rate(
            utilization,
            optimal_usage_ratio,
            base_variable_borrow_rate,
            variable_rate_slope1,
            variable_rate_slope2
        )
        
        # Calculate supply APY: borrowRate * utilizationRate * (1 - reserveFactor)
        supply_apy = borrow_rate * utilization * (1 - reserve_factor)
        
        # Add rewards APY if available
        rewards_apy = 0.0
        if reserve_data.get('rewards_per_year', 0) > 0 and reserve_data.get('token_price', 0) > 0:
            rewards_apr = reserve_data['rewards_per_year'] / (total_supplied * reserve_data['token_price'])
            rewards_apy = (1 + rewards_apr/365)**365 - 1
        
        total_apy = supply_apy + rewards_apy
        
        # Calculate APR from APY using the formula: APR = 365 * [(1 + APY)^(1/365) - 1]
        supply_apr = 365 * ((1 + supply_apy) ** (1/365) - 1) if supply_apy > 0 else 0
        rewards_apr = 365 * ((1 + rewards_apy) ** (1/365) - 1) if rewards_apy > 0 else 0
        total_apr = supply_apr + rewards_apr
        
        return supply_apy, rewards_apy, total_apy, supply_apr, rewards_apr, total_apr
    
    @classmethod
    def calculate_pool_apr_apy(cls, our_supply, pool_data):
        """
        AAVE doesn't have pools in this context, implementing for compatibility
        """
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    
    @classmethod
    def get_aave_abi(cls):
        """
        Load the AAVE ABI from file
        """
        try:
            with open('protocols/abi/aave_abi.json', 'r') as f:
                return json.load(f)
        except Exception:
            return None
    
    @classmethod
    def get_reserve_abi(cls):
        """
        Load the Reserve ABI from file
        """
        try:
            with open('protocols/abi/reserve_abi.json', 'r') as f:
                return json.load(f)
        except Exception:
            return None
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        AAVE doesn't have pools in this context, only reserves
        """
        return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch and process AAVE reserve data
        This version expects aToken address and extracts underlying token address
        """
        try:
            atoken_address = fund_data.get('address')
            if not atoken_address:
                return None
            atoken_address = Web3.to_checksum_address(atoken_address)
            # Underlying address (cached forever)
            underlying_address = cls.get_underlying_asset_address(atoken_address)
            if not underlying_address:
                logging.error(f"Could not get underlying asset address from aToken {atoken_address}")
                return None
            underlying_address = Web3.to_checksum_address(underlying_address)
            w3 = cls.get_web3_provider()
            # Token info (cached forever)
            token_cache_key = f"token_info_{underlying_address}"
            cached_token_info = cache_manager.get_cached_data('aave_v3', 'sonic', token_cache_key)
            if cached_token_info:
                token_name = cached_token_info['token_name']
                decimals = cached_token_info['decimals']
            else:
                try:
                    token_contract = w3.eth.contract(address=underlying_address, abi=[
                        {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
                        {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
                    ])
                    decimals = token_contract.functions.decimals().call()
                    token_name = token_contract.functions.symbol().call()
                    cache_manager.set_cached_data('aave_v3', 'sonic', token_cache_key, {
                        'token_name': token_name,
                        'decimals': decimals
                    }, ttl=None)
                except Exception:
                    decimals = 18
                    token_name = "USDC"
            # Dynamic data (30 min TTL)
            dynamic_cache_key = f"dynamic_{underlying_address}"
            cached_dynamic = cache_manager.get_cached_data('aave_v3', 'sonic', dynamic_cache_key)
            if cached_dynamic:
                reserve_data = cached_dynamic['reserve_data']
                unbacked = cached_dynamic['unbacked']
                total_borrowed = cached_dynamic['total_borrowed']
                total_supplied = cached_dynamic['total_supplied']
                utilization_rate = cached_dynamic['utilization_rate']
            else:
                try:
                    aave_abi = cls.get_aave_abi()
                    aave_contract = w3.eth.contract(
                        address=Web3.to_checksum_address(cls.AAVE_CONTRACT_ADDRESS),
                        abi=aave_abi
                    )
                    reserve_data = aave_contract.functions.getReserveData(underlying_address).call()
                except Exception as e:
                    logging.error(f"Error fetching dynamic data for {underlying_address}: {str(e)}")
                    return None
                def scale_token_amount(value):
                    return float(value) / (10 ** decimals)
                unbacked = scale_token_amount(reserve_data[0])
                total_borrowed = scale_token_amount(reserve_data[4])
                total_supplied = scale_token_amount(reserve_data[2])
                utilization_rate = total_borrowed / total_supplied if total_supplied > 0 else 0
                cache_manager.set_cached_data('aave_v3', 'sonic', dynamic_cache_key, {
                    'reserve_data': reserve_data,
                    'unbacked': unbacked,
                    'total_borrowed': total_borrowed,
                    'total_supplied': total_supplied,
                    'utilization_rate': utilization_rate
                }, ttl=1800)  # 30 хв
            # Static data (7 днів TTL)
            static_cache_key = f"static_{underlying_address}"
            cached_static = cache_manager.get_cached_data('aave_v3', 'sonic', static_cache_key)
            if cached_static:
                config_data = cached_static['config_data']
                interest_rate_data = cached_static['interest_rate_data']
                rate_strategy_address = cached_static['rate_strategy_address']
            else:
                try:
                    aave_abi = cls.get_aave_abi()
                    aave_contract = w3.eth.contract(
                        address=Web3.to_checksum_address(cls.AAVE_CONTRACT_ADDRESS),
                        abi=aave_abi
                    )
                    rate_strategy_address = aave_contract.functions.getInterestRateStrategyAddress(underlying_address).call()
                    rate_strategy_address = Web3.to_checksum_address(rate_strategy_address)
                    config_data = aave_contract.functions.getReserveConfigurationData(underlying_address).call()
                    reserve_abi = cls.get_reserve_abi()
                    if not reserve_abi:
                        return None
                    rate_strategy_contract = w3.eth.contract(
                        address=rate_strategy_address,
                        abi=reserve_abi
                    )
                    interest_rate_data = rate_strategy_contract.functions.getInterestRateDataBps(underlying_address).call()
                except Exception as e:
                    logging.error(f"Error fetching static data for {underlying_address}: {str(e)}")
                    return None
                cache_manager.set_cached_data('aave_v3', 'sonic', static_cache_key, {
                    'config_data': config_data,
                    'interest_rate_data': interest_rate_data,
                    'rate_strategy_address': rate_strategy_address
                }, ttl=604800)  # 7 днів
            def bps_to_percent(bps_value):
                return float(bps_value) / 100
            reserve_info = {
                'id': atoken_address,  # Use aToken address as ID
                'name': f"{token_name} Reserve",
                'protocol': cls.PROTOCOL_NAME,
                'total_borrowed': total_borrowed,
                'total_supplied': total_supplied,
                'unbacked': unbacked,
                'utilization_rate': utilization_rate,  # Add utilization rate to output
                'optimal_usage_ratio': bps_to_percent(interest_rate_data[0])/100,
                'variable_rate_slope1': bps_to_percent(interest_rate_data[2])/100,
                'variable_rate_slope2': bps_to_percent(interest_rate_data[3])/100,
                'token_price': 1.0,
                'fee_percentage': 0.0,
                'base_variable_borrow_rate': bps_to_percent(interest_rate_data[1])/100,
                'reserve_factor': float(config_data[4]) / 10000,
                'source': cls.SOURCE,
                'network': cls.NETWORK_NAME,
                'rewards_per_year': 0,
                'type': 'reserve'
            }
            return reserve_info
        except Exception as e:
            logging.error(f"Error processing AAVE reserve: {str(e)}")
            return None 