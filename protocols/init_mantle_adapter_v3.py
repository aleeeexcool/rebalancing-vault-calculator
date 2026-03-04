"""
Init Protocol Adapter V3 for Mantle network
This adapter receives aToken address and extracts underlying token address from it
"""
import logging
import json
import os
from typing import Dict, Optional, List
from decimal import Decimal
from web3 import Web3
import requests
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_reserve_name
from .core.cache_manager import cache_manager

# Load ABIs from JSON files
def load_abi(filename):
    """
    Load ABI from a JSON file
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    abi_path = os.path.join(current_dir, 'abi', filename)
    try:
        with open(abi_path, 'r') as f:
            return json.load(f)
    except Exception:
        return []

# Load contract ABIs
INIT_POOL_ABI = load_abi('init_pool_abi.json')
INIT_IRM_ABI = load_abi('init_irm_abi.json')
INIT_ORACLE_ABI = load_abi('init_oracle_abi.json')
INIT_ATOKEN_ABI = load_abi('init_atoken_abi.json')  # For getting underlying address

class InitMantleAdapterV3(BaseProtocolAdapter):
    """
    Adapter for Init protocol V3 on Mantle network
    This version receives aToken address and extracts underlying token address
    """
    SOURCE = "Init Mantle V3"
    PROTOCOL_NAME = "Init"
    NETWORK_NAME = "Mantle"
    
    # Mantle RPC URLs
    RPC_URLS = [
        "https://rpc.mantle.xyz",
        "https://mantle-mainnet.public.blastapi.io"
    ]
    
    # Init protocol contract addresses
    INIT_ORACLE_ADDRESS = "0x4E195A32b2f6eBa9c4565bA49bef34F23c2C0350"
    
    # Seconds in a year for calculations
    SECONDS_IN_YEAR = 31536000
    
    @classmethod
    def get_web3_provider(cls):
        """
        Get Web3 provider for Mantle network with fallback
        
        Returns:
            Web3: Web3 provider instance
        """
        cached_provider_data = cache_manager.get_cached_data('init_v3', 'mantle', 'web3_provider')
        if cached_provider_data:
            try:
                cached_rpc_url = cached_provider_data.get('rpc_url')
                if cached_rpc_url:
                    return Web3(Web3.HTTPProvider(cached_rpc_url))
            except Exception:
                cache_manager.invalidate_cache('init_v3', 'mantle', 'web3_provider')
        
        for rpc_url in cls.RPC_URLS:
            try:
                provider = Web3(Web3.HTTPProvider(rpc_url))
                if provider.is_connected():
                    cache_manager.set_cached_data('init_v3', 'mantle', 'web3_provider', {
                        'rpc_url': rpc_url
                    }, ttl=900)
                    return provider
            except Exception:
                continue
        
        raise Exception("Failed to connect to any Mantle RPC")
    
    @classmethod
    def get_underlying_asset_address(cls, atoken_address: str) -> str:
        """
        Get underlying asset address from aToken address
        
        Args:
            atoken_address: Address of the aToken
            
        Returns:
            str: Address of the underlying asset
        """
        cache_key = f"underlying_{atoken_address.lower()}"
        cached_address = cache_manager.get_cached_data('init_v3', 'mantle', cache_key)
        if cached_address:
            return cached_address.get('underlying_address')
        
        try:
            web3 = cls.get_web3_provider()
            atoken_contract = web3.eth.contract(
                address=Web3.to_checksum_address(atoken_address),
                abi=INIT_ATOKEN_ABI
            )
            
            underlying_address = atoken_contract.functions.underlyingToken().call()
            
            cache_manager.set_cached_data('init_v3', 'mantle', cache_key, {
                'underlying_address': underlying_address
            }, ttl=86400)
            
            return underlying_address
        except Exception:
            return None
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from Init protocol V3
        """
        return fund_data.get('source') == cls.SOURCE
    
    @classmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        """
        Calculate borrow rate for Init reserves using the formula:
        borrow rate = baseRate + m1 * min(uti, jumpUtil) + m2 * max(0, uti - jumpUtil)
        
        Args:
            utilization: Current utilization rate
            optimal_usage_ratio: Optimal utilization rate (jump point)
            base_variable_borrow_rate: Base variable borrow rate
            variable_rate_slope1: Slope 1 (before jump)
            variable_rate_slope2: Slope 2 (after jump)
            
        Returns:
            float: Calculated borrow APY
        """
        borrow_rate = base_variable_borrow_rate + (variable_rate_slope1 * min(utilization, optimal_usage_ratio))
        if utilization > optimal_usage_ratio:
            excess = utilization - optimal_usage_ratio
            borrow_rate += variable_rate_slope2 * excess
        
        return borrow_rate
    
    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY for Init reserve
        
        Args:
            our_supply: Amount we're planning to supply
            reserve_data: Reserve data dictionary
            
        Returns:
            tuple: (reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr)
        """
        total_borrowed = reserve_data.get('total_borrowed', 0)
        total_supplied = reserve_data.get('total_supplied', 0)
        utilization = (total_borrowed / (total_supplied + our_supply)) if (total_supplied + our_supply) > 0 else 0
        
        borrow_apy = cls.calculate_borrow_rate(
            utilization,
            reserve_data.get('optimal_usage_ratio', 0.8),
            reserve_data.get('base_variable_borrow_rate', 0),
            reserve_data.get('variable_rate_slope1', 0.08),
            reserve_data.get('variable_rate_slope2', 0.8)
        )
        
        # Get reserve factor
        reserve_factor = reserve_data.get('reserve_factor', reserve_data.get('fee_percentage', 0))
        
        # Calculate supply APY
        reserve_apy = borrow_apy * utilization * (1 - reserve_factor)
        
        # Add rewards APY if available
        rewards_apy = 0.0
        if reserve_data.get('rewards_per_year', 0) > 0 and reserve_data.get('token_price', 0) > 0:
            rewards_apr = reserve_data['rewards_per_year'] / ((total_supplied + our_supply) * reserve_data['token_price'])
            rewards_apy = (1 + rewards_apr/365)**365 - 1
        
        total_apy = reserve_apy + rewards_apy
        
        # Calculate APR from APY using the formula: APR = 365 * [(1 + APY)^(1/365) - 1]
        reserve_apr = 365 * ((1 + reserve_apy) ** (1/365) - 1) if reserve_apy > 0 else 0
        rewards_apr = 365 * ((1 + rewards_apy) ** (1/365) - 1) if rewards_apy > 0 else 0
        total_apr = reserve_apr + rewards_apr
        
        return reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        Init doesn't have pools in this context, only reserves
        """
        return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch and process Init reserve data
        This version expects aToken address and extracts underlying token address
        """
        try:
            # Get aToken address from fund_data
            atoken_address = fund_data.get('address')
            if not atoken_address:
                logging.error("[INIT] Missing address in fund_data")
                return None
                
            atoken_address = Web3.to_checksum_address(atoken_address)
            
            # Extract underlying token address from aToken
            underlying_address = cls.get_underlying_asset_address(atoken_address)
            if not underlying_address:
                logging.error(f"Could not get underlying asset address from aToken {atoken_address}")
                return None
                
            underlying_address = Web3.to_checksum_address(underlying_address)
            
            # Try to get cached static data (1 day TTL)
            static_cache_key = f"static_{underlying_address}"
            cached_static = cache_manager.get_cached_data('init_v3', 'mantle', static_cache_key)
            
            # Try to get cached dynamic data (10 min TTL)
            dynamic_cache_key = f"dynamic_{underlying_address}"
            cached_dynamic = cache_manager.get_cached_data('init_v3', 'mantle', dynamic_cache_key)
            
            # Get web3 provider
            web3 = cls.get_web3_provider()
            
            # Initialize aToken contract
            atoken_contract = web3.eth.contract(address=atoken_address, abi=INIT_ATOKEN_ABI)
            
            # Ініціалізую oracle_contract перед першим використанням
            oracle_contract = web3.eth.contract(
                address=cls.INIT_ORACLE_ADDRESS, 
                abi=INIT_ORACLE_ABI
            )
            
            # Get dynamic data
            if cached_dynamic:
                formatted_total_assets = cached_dynamic['total_supplied']
                formatted_total_debt = cached_dynamic['total_borrowed']
            else:
                # Batch dynamic data calls
                try:
                    batch_calls = [
                        atoken_contract.functions.decimals(),
                        atoken_contract.functions.totalAssets(),
                        atoken_contract.functions.totalDebt()
                    ]
                    
                    pool_decimals = batch_calls[0].call()
                    total_assets = batch_calls[1].call()
                    total_debt = batch_calls[2].call()
                except Exception:
                    pool_decimals = atoken_contract.functions.decimals().call()
                    total_assets = atoken_contract.functions.totalAssets().call()
                    total_debt = atoken_contract.functions.totalDebt().call()
                
                adjusted_decimals = pool_decimals - 8
                formatted_total_assets = float(total_assets) / (10 ** adjusted_decimals)
                formatted_total_debt = float(total_debt) / (10 ** adjusted_decimals)
                
                cache_manager.set_cached_data('init_v3', 'mantle', dynamic_cache_key, {
                    'total_supplied': formatted_total_assets,
                    'total_borrowed': formatted_total_debt
                }, ttl=600)
            
            # Calculate utilization rate with safety check
            if formatted_total_assets > 0:
                utilization_rate = formatted_total_debt / formatted_total_assets
                
                # Log warning if utilization is anomalously high
                if utilization_rate > 1.0:
                    utilization_rate = min(utilization_rate, 1.0)
            else:
                utilization_rate = 0
            
            # Get static data
            if cached_static:
                reserve_name = cached_static['name']
                optimal_utilization_rate = cached_static['optimal_usage_ratio']
                slope1 = cached_static['variable_rate_slope1']
                slope2 = cached_static['variable_rate_slope2']
                price_usd = cached_static['token_price']
                reserve_factor = cached_static['reserve_factor']
            else:
                # Fetch fresh static data
                # Get IRM (Interest Rate Model) address
                irm_address = atoken_contract.functions.irm().call()
                
                # Initialize IRM contract
                irm_contract = web3.eth.contract(address=irm_address, abi=INIT_IRM_ABI)
                
                # Get IRM data
                # Batch static calls
                try:
                    static_batch_calls = [
                        irm_contract.functions.BORR_RATE_MULTIPLIER_E18(),
                        irm_contract.functions.JUMP_MULTIPLIER_E18(),
                        irm_contract.functions.JUMP_UTIL_E18(),
                        oracle_contract.functions.getPrice_e36(underlying_address)
                    ]
                    
                    borrow_rate_multiplier = static_batch_calls[0].call()
                    jump_multiplier = static_batch_calls[1].call()
                    jump_util = static_batch_calls[2].call()
                    price_e36 = static_batch_calls[3].call()
                except Exception:
                    borrow_rate_multiplier = irm_contract.functions.BORR_RATE_MULTIPLIER_E18().call()
                    jump_multiplier = irm_contract.functions.JUMP_MULTIPLIER_E18().call()
                    jump_util = irm_contract.functions.JUMP_UTIL_E18().call()
                    price_e36 = oracle_contract.functions.getPrice_e36(underlying_address).call()
                
                # Calculate rates
                E18 = 10 ** 18
                slope1 = (borrow_rate_multiplier / E18) * cls.SECONDS_IN_YEAR
                slope2 = (jump_multiplier / E18) * cls.SECONDS_IN_YEAR
                optimal_utilization_rate = jump_util / E18
                
                # Get reserve factor
                try:
                    reserve_factor_e18 = atoken_contract.functions.reserveFactor_e18().call()
                    reserve_factor = reserve_factor_e18 / E18
                except Exception as e:
                    logging.warning(f"Error getting reserve factor for aToken {atoken_address}: {e}")
                    reserve_factor = 0
                
                # Get price from Oracle
                price_e36 = oracle_contract.functions.getPrice_e36(underlying_address).call()
                
                # Get pool decimals for price calculation
                pool_decimals = atoken_contract.functions.decimals().call()
                adjusted_decimals = pool_decimals - 8
                price_usd = float(price_e36) / (10 ** (36 - adjusted_decimals))
                
                # Get reserve name from smart contract
                base_reserve_name = get_reserve_name(underlying_address)
                if not base_reserve_name:
                    base_reserve_name = f'Reserve-{underlying_address[:8]}'
                
                # Create unique reserve name
                reserve_name = f"{base_reserve_name} Reserve"
                
                # Cache static data for 1 day
                cache_manager.set_cached_data('init_v3', 'mantle', static_cache_key, {
                    'name': reserve_name,
                    'optimal_usage_ratio': optimal_utilization_rate,
                    'variable_rate_slope1': slope1,
                    'variable_rate_slope2': slope2,
                    'token_price': price_usd,
                    'reserve_factor': reserve_factor
                }, ttl=86400)

            reserve_info = {
                'id': atoken_address,  # Use aToken address as ID
                'name': reserve_name,
                'protocol': cls.PROTOCOL_NAME,
                'total_borrowed': formatted_total_debt,
                'total_supplied': formatted_total_assets,
                'utilization_rate': utilization_rate,
                'optimal_usage_ratio': optimal_utilization_rate,
                'variable_rate_slope1': slope1,
                'variable_rate_slope2': slope2,
                'token_price': price_usd,
                'fee_percentage': 0,
                'base_variable_borrow_rate': 0,  # Default base rate is 0 for Init
                'reserve_factor': reserve_factor,
                'source': cls.SOURCE,
                'network': cls.NETWORK_NAME,
                'rewards_per_year': 0,  # Init doesn't have rewards in this context
                'type': 'reserve'
            }
            
            return reserve_info
            
        except Exception as e:
            logging.error(f"Error processing Init reserve {fund_data.get('address')}: {str(e)}")
            return None 