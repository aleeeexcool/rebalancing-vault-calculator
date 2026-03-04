"""
AAVE Protocol Adapter V3 for Base network
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

class AaveBaseAdapterV3(BaseProtocolAdapter):
    """
    Adapter for AAVE protocol V3 on Base network
    This version receives aToken address and extracts underlying token address
    """
    
    SOURCE = "Aave Base V3"
    PROTOCOL_NAME = "Aave"
    NETWORK_NAME = "Base"
    
    # Base RPC URLs with fallback
    RPC_URLS = [
        "https://mainnet.base.org",
        "https://base-mainnet.public.blastapi.io",
        "https://1rpc.io/base"
    ]
    
    AAVE_CONTRACT_ADDRESS = "0xC4Fcf9893072d61Cc2899C0054877Cb752587981"  # Main AAVE contract on Base
    
    @classmethod
    def get_web3_provider(cls):
        """
        Get Web3 provider for Base network with fallback and caching
        
        Returns:
            Web3: Web3 provider instance
        """
        cached_provider_data = cache_manager.get_cached_data('aave_v3', 'base', 'web3_provider')
        if cached_provider_data:
            try:
                cached_rpc_url = cached_provider_data.get('rpc_url')
                if cached_rpc_url:
                    return Web3(Web3.HTTPProvider(cached_rpc_url))
            except Exception:
                cache_manager.invalidate_cache('aave_v3', 'base', 'web3_provider')
        
        for rpc_url in cls.RPC_URLS:
            try:
                provider = Web3(Web3.HTTPProvider(rpc_url))
                if provider.is_connected():
                    cache_manager.set_cached_data('aave_v3', 'base', 'web3_provider', {
                        'rpc_url': rpc_url
                    }, ttl=900)
                    return provider
            except Exception:
                continue
        
        raise Exception("Failed to connect to any Base RPC")
    
    @classmethod
    def get_underlying_asset_address(cls, atoken_address: str) -> str:
        """
        Get underlying asset address from aToken address with permanent caching
        
        Args:
            atoken_address: Address of the aToken
            
        Returns:
            str: Address of the underlying asset
        """
        cache_key = f"underlying_{atoken_address.lower()}"
        cached_address = cache_manager.get_cached_data('aave_v3', 'base', cache_key)
        if cached_address:
            return cached_address.get('underlying_address')
        
        try:
            w3 = cls.get_web3_provider()
            atoken_contract = w3.eth.contract(
                address=Web3.to_checksum_address(atoken_address),
                abi=AAVE_ATOKEN_ABI
            )
            
            # Try UNDERLYING_ASSET_ADDRESS first, fallback to asset()
            try:
                underlying_address = atoken_contract.functions.UNDERLYING_ASSET_ADDRESS().call()
            except:
                # Fallback to asset() function if UNDERLYING_ASSET_ADDRESS doesn't exist
                underlying_address = atoken_contract.functions.asset().call()
                
            cache_manager.set_cached_data('aave_v3', 'base', cache_key, {
                'underlying_address': underlying_address
            }, ttl=None)  # Cache forever - underlying address never changes
            
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
            with open('protocols/abi/aave_base_abi.json', 'r') as f:
                return json.load(f)
        except Exception:
            # Return hardcoded ABI if file not found
            return [{"inputs":[{"internalType":"contract IPoolAddressesProvider","name":"addressesProvider","type":"address"}],"stateMutability":"nonpayable","type":"constructor"},{"inputs":[],"name":"ADDRESSES_PROVIDER","outputs":[{"internalType":"contract IPoolAddressesProvider","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getATokenTotalSupply","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"getAllATokens","outputs":[{"components":[{"internalType":"string","name":"symbol","type":"string"},{"internalType":"address","name":"tokenAddress","type":"address"}],"internalType":"struct IPoolDataProvider.TokenData[]","name":"","type":"tuple[]"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"getAllReservesTokens","outputs":[{"components":[{"internalType":"string","name":"symbol","type":"string"},{"internalType":"address","name":"tokenAddress","type":"address"}],"internalType":"struct IPoolDataProvider.TokenData[]","name":"","type":"tuple[]"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getDebtCeiling","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"getDebtCeilingDecimals","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"pure","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getFlashLoanEnabled","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getInterestRateStrategyAddress","outputs":[{"internalType":"address","name":"irStrategyAddress","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getIsVirtualAccActive","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getLiquidationProtocolFee","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getPaused","outputs":[{"internalType":"bool","name":"isPaused","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveCaps","outputs":[{"internalType":"uint256","name":"borrowCap","type":"uint256"},{"internalType":"uint256","name":"supplyCap","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveConfigurationData","outputs":[{"internalType":"uint256","name":"decimals","type":"uint256"},{"internalType":"uint256","name":"ltv","type":"uint256"},{"internalType":"uint256","name":"liquidationThreshold","type":"uint256"},{"internalType":"uint256","name":"liquidationBonus","type":"uint256"},{"internalType":"uint256","name":"reserveFactor","type":"uint256"},{"internalType":"bool","name":"usageAsCollateralEnabled","type":"bool"},{"internalType":"bool","name":"borrowingEnabled","type":"bool"},{"internalType":"bool","name":"stableBorrowRateEnabled","type":"bool"},{"internalType":"bool","name":"isActive","type":"bool"},{"internalType":"bool","name":"isFrozen","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveData","outputs":[{"internalType":"uint256","name":"unbacked","type":"uint256"},{"internalType":"uint256","name":"accruedToTreasuryScaled","type":"uint256"},{"internalType":"uint256","name":"totalAToken","type":"uint256"},{"internalType":"uint256","name":"","type":"uint256"},{"internalType":"uint256","name":"totalVariableDebt","type":"uint256"},{"internalType":"uint256","name":"liquidityRate","type":"uint256"},{"internalType":"uint256","name":"variableBorrowRate","type":"uint256"},{"internalType":"uint256","name":"","type":"uint256"},{"internalType":"uint256","name":"","type":"uint256"},{"internalType":"uint256","name":"liquidityIndex","type":"uint256"},{"internalType":"uint256","name":"variableBorrowIndex","type":"uint256"},{"internalType":"uint40","name":"lastUpdateTimestamp","type":"uint40"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveDeficit","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveTokensAddresses","outputs":[{"internalType":"address","name":"aTokenAddress","type":"address"},{"internalType":"address","name":"stableDebtTokenAddress","type":"address"},{"internalType":"address","name":"variableDebtTokenAddress","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getSiloedBorrowing","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getTotalDebt","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getUnbackedMintCap","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"},{"internalType":"address","name":"user","type":"address"}],"name":"getUserReserveData","outputs":[{"internalType":"uint256","name":"currentATokenBalance","type":"uint256"},{"internalType":"uint256","name":"currentStableDebt","type":"uint256"},{"internalType":"uint256","name":"currentVariableDebt","type":"uint256"},{"internalType":"uint256","name":"principalStableDebt","type":"uint256"},{"internalType":"uint256","name":"scaledVariableDebt","type":"uint256"},{"internalType":"uint256","name":"stableBorrowRate","type":"uint256"},{"internalType":"uint256","name":"liquidityRate","type":"uint256"},{"internalType":"uint40","name":"stableRateLastUpdated","type":"uint40"},{"internalType":"bool","name":"usageAsCollateralEnabled","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getVirtualUnderlyingBalance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
    
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
        Fetch and process AAVE reserve data with optimized caching
        This version expects aToken address and extracts underlying token address
        """
        try:
            # Get aToken address from fund_data
            atoken_address = fund_data.get('address')
            if not atoken_address:
                logging.error("[AAVE] Missing address in fund_data")
                return None
            atoken_address = Web3.to_checksum_address(atoken_address)
            
            # Extract underlying token address from aToken (cached forever)
            underlying_address = cls.get_underlying_asset_address(atoken_address)
            if not underlying_address:
                logging.error(f"Could not get underlying asset address from aToken {atoken_address}")
                return None
                
            underlying_address = Web3.to_checksum_address(underlying_address)
            
            # Try to get cached token info (permanent cache)
            token_cache_key = f"token_info_{underlying_address}"
            cached_token_info = cache_manager.get_cached_data('aave_v3', 'base', token_cache_key)
            
            # Try to get cached static data (7 days TTL - config that rarely changes)
            static_cache_key = f"static_{underlying_address}"
            cached_static = cache_manager.get_cached_data('aave_v3', 'base', static_cache_key)
            
            # Try to get cached dynamic data (30 min TTL - frequently changing data)
            dynamic_cache_key = f"dynamic_{underlying_address}"
            cached_dynamic = cache_manager.get_cached_data('aave_v3', 'base', dynamic_cache_key)
            
            # Get web3 provider
            w3 = cls.get_web3_provider()
            
            # Use underlying address for all contract calls
            aave_contract_address = Web3.to_checksum_address(cls.AAVE_CONTRACT_ADDRESS)
            aave_abi = cls.get_aave_abi()
            
            if not aave_abi:
                return None
                
            aave_contract = w3.eth.contract(
                address=aave_contract_address,
                abi=aave_abi
            )
            
            # Get token info (cache forever - never changes)
            if cached_token_info:
                token_name = cached_token_info['token_name']
                decimals = cached_token_info['decimals']
            else:
                # Get token info
                try:
                    token_contract = w3.eth.contract(address=underlying_address, abi=[
                        {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
                        {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
                    ])
                    decimals = token_contract.functions.decimals().call()
                    token_name = token_contract.functions.symbol().call()
                    
                    # Cache token info forever
                    cache_manager.set_cached_data('aave_v3', 'base', token_cache_key, {
                        'token_name': token_name,
                        'decimals': decimals
                    }, ttl=None)
                    
                except Exception:
                    decimals = 18
                    token_name = "USDC"
            
            # Get dynamic data (reserve data that changes frequently)
            if cached_dynamic:
                reserve_data = cached_dynamic['reserve_data']
                unbacked = cached_dynamic['unbacked']
                total_borrowed = cached_dynamic['total_borrowed']
                total_supplied = cached_dynamic['total_supplied']
                utilization_rate = cached_dynamic['utilization_rate']
            else:
                # Fetch fresh dynamic data
                try:
                    reserve_data = aave_contract.functions.getReserveData(underlying_address).call()
                        
                except Exception as e:
                    logging.error(f"Error fetching dynamic data for {underlying_address}: {str(e)}")
                    return None
                
                def scale_token_amount(value):
                    return float(value) / (10 ** decimals)
                    
                # Get unbacked value from reserve data
                unbacked = scale_token_amount(reserve_data[0])  # First item in reserve_data is unbacked
                    
                total_borrowed = scale_token_amount(reserve_data[4])
                total_supplied = scale_token_amount(reserve_data[2])
                
                # Calculate utilization rate
                utilization_rate = total_borrowed / total_supplied if total_supplied > 0 else 0
                
                # Cache dynamic data for 30 minutes
                cache_manager.set_cached_data('aave_v3', 'base', dynamic_cache_key, {
                    'reserve_data': reserve_data,
                    'unbacked': unbacked,
                    'total_borrowed': total_borrowed,
                    'total_supplied': total_supplied,
                    'utilization_rate': utilization_rate
                }, ttl=1800)  # 30 minutes
            
            # Get static data (configuration data that rarely changes)
            if cached_static:
                config_data = cached_static['config_data']
                interest_rate_data = cached_static['interest_rate_data']
                rate_strategy_address = cached_static['rate_strategy_address']
            else:
                # Fetch fresh static data
                try:
                    # Get rate strategy address and config data
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
                
                # Cache static data for 7 days (config changes are rare via governance)
                cache_manager.set_cached_data('aave_v3', 'base', static_cache_key, {
                    'config_data': config_data,
                    'interest_rate_data': interest_rate_data,
                    'rate_strategy_address': rate_strategy_address
                }, ttl=604800)  # 7 days
                
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
            logging.error(f"Error processing AAVE reserve on Base: {str(e)}")
            return None 