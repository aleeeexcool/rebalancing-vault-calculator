"""
Init Protocol Adapter for Mantle network
"""
import logging
import requests
import json
import os
from typing import Dict, List, Optional
from web3 import Web3
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
    with open(abi_path, 'r') as file:
        return json.load(file)

# Load contract ABIs
INIT_POOL_ABI = load_abi('init_pool_abi.json')
INIT_IRM_ABI = load_abi('init_irm_abi.json')
INIT_ORACLE_ABI = load_abi('init_oracle_abi.json')

class InitAdapter(BaseProtocolAdapter):
    """
    Adapter for Init protocol on Mantle network
    """
    SOURCE = "Init Mantle"
    PROTOCOL_NAME = "Init"
    NETWORK_NAME = "Mantle"
    
    # Mantle RPC URLs
    RPC_URLS = [
        "https://rpc.mantle.xyz",
        "https://mantle-mainnet.public.blastapi.io"
    ]
    
    # Init protocol contract addresses
    INIT_ORACLE_ADDRESS = "0x4E195A32b2f6eBa9c4565bA49bef34F23c2C0350"
    
    # Init pools URL
    INIT_POOLS_URL = "https://app.init.capital/static/json/pools.json"
    
    # Seconds in a year for calculations
    SECONDS_IN_YEAR = 31536000
    
    @classmethod
    def get_web3_provider(cls):
        """
        Get Web3 provider for Mantle network with fallback
        
        Returns:
            Web3: Web3 provider instance
        """
        cached_provider_data = cache_manager.get_cached_data('init', 'mantle', 'web3_provider')
        if cached_provider_data:
            try:
                cached_rpc_url = cached_provider_data.get('rpc_url')
                if cached_rpc_url:
                    return Web3(Web3.HTTPProvider(cached_rpc_url))
            except Exception:
                cache_manager.invalidate_cache('init', 'mantle', 'web3_provider')
        
        for rpc_url in cls.RPC_URLS:
            try:
                provider = Web3(Web3.HTTPProvider(rpc_url))
                if provider.is_connected():
                    cache_manager.set_cached_data('init', 'mantle', 'web3_provider', {
                        'rpc_url': rpc_url
                    }, ttl=900)
                    return provider
            except Exception:
                continue
        
        raise Exception("Failed to connect to any Mantle RPC")
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from Init protocol
        """
        # Check in source field
        if fund_data.get('source') == cls.SOURCE:
            return True
            
        # Additional detection logic if needed
        return False
    
    @classmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        """
        Calculate borrow rate for INIT reserves using the formula:
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
    def get_init_pools_data(cls):
        """
        Fetch Init pools data from the official Init website
        
        Returns:
            dict: Init pools data by pool address
        """
        cached_pools = cache_manager.get_cached_data('init', 'mantle', 'pools_data')
        if cached_pools:
            return cached_pools.get('pools', {})
        
        try:
            # Fetch fresh pools data
            response = requests.get(cls.INIT_POOLS_URL)
            if response.status_code != 200:
                logging.error(f"Failed to fetch Init pools data: {response.text}")
                return {}
            
            pools_data = response.json()
            # Get Mantle network pools (chainId 5000)
            mantle_pools = pools_data.get('5000', {})
            
            # Cache pools data for 6 hours
            cache_manager.set_cached_data('init', 'mantle', 'pools_data', {
                'pools': mantle_pools
            }, ttl=21600)
            
            return mantle_pools
        except Exception as e:
            logging.error(f"Error fetching Init pools data: {e}")
            return {}
    
    @classmethod
    def get_init_pool_by_token(cls, token_address):
        """
        Find the Init pool for a given token
        
        Args:
            token_address: Token address to find pool for
            
        Returns:
            tuple: (pool_address, pool_info) or (None, None) if not found
        """
        token_address = token_address.lower()
        pools_data = cls.get_init_pools_data()
        
        for pool_address, pool_info in pools_data.items():
            if pool_info.get('isHide'):
                continue
                
            if pool_info.get('underlyingToken', '').lower() == token_address:
                return pool_address, pool_info
                
        return None, None
    
    @classmethod
    def get_reserve_rewards(cls, reserve_name: str) -> float:
        """
        Get rewards for Init reserve - delegating to Lendle adapter for now
        """
        return 0
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        Init doesn't have pools in this context, only reserves
        """
        return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch and process Init reserve data directly from the blockchain
        """
        try:
            address = Web3.to_checksum_address(fund_data['address'])
            
            # Try to get cached static data (1 day TTL)
            static_cache_key = f"static_{address}"
            cached_static = cache_manager.get_cached_data('init', 'mantle', static_cache_key)
            
            # Try to get cached dynamic data (10 min TTL)
            dynamic_cache_key = f"dynamic_{address}"
            cached_dynamic = cache_manager.get_cached_data('init', 'mantle', dynamic_cache_key)
            
            # Find Init pool for this token
            pool_address, pool_info = cls.get_init_pool_by_token(address)
            if not pool_address:
                logging.error(f"No Init pool found for token {address}")
                return None
                
            pool_address = Web3.to_checksum_address(pool_address)
            
            # Get web3 provider
            web3 = cls.get_web3_provider()
            
            # Initialize pool contract
            pool_contract = web3.eth.contract(address=pool_address, abi=INIT_POOL_ABI)
            
            # Initialize oracle contract
            oracle_contract = web3.eth.contract(address=cls.INIT_ORACLE_ADDRESS, abi=INIT_ORACLE_ABI)
            
            # Get dynamic data (total_assets, total_debt)
            if cached_dynamic:
                formatted_total_assets = cached_dynamic['total_supplied']
                formatted_total_debt = cached_dynamic['total_borrowed']
            else:
                # Batch dynamic data calls
                try:
                    batch_calls = [
                        pool_contract.functions.decimals(),
                        pool_contract.functions.totalAssets(),
                        pool_contract.functions.totalDebt()
                    ]
                    
                    pool_decimals = batch_calls[0].call()
                    total_assets = batch_calls[1].call()
                    total_debt = batch_calls[2].call()
                except Exception:
                    pool_decimals = pool_contract.functions.decimals().call()
                    total_assets = pool_contract.functions.totalAssets().call()
                    total_debt = pool_contract.functions.totalDebt().call()
                
                adjusted_decimals = pool_decimals - 8
                formatted_total_assets = float(total_assets) / (10 ** adjusted_decimals)
                formatted_total_debt = float(total_debt) / (10 ** adjusted_decimals)
                
                # Cache dynamic data for 10 minutes
                cache_manager.set_cached_data('init', 'mantle', dynamic_cache_key, {
                    'total_supplied': formatted_total_assets,
                    'total_borrowed': formatted_total_debt
                }, ttl=600)
            
            # Calculate utilization rate from cached/fresh data
            if formatted_total_assets > 0:
                utilization_rate = formatted_total_debt / formatted_total_assets
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
                rewards_per_year = cached_static['rewards_per_year']
            else:
                # Fetch fresh static data
                irm_address = pool_contract.functions.irm().call()
                irm_contract = web3.eth.contract(address=irm_address, abi=INIT_IRM_ABI)
                
                # Batch static calls
                try:
                    static_batch_calls = [
                        irm_contract.functions.BORR_RATE_MULTIPLIER_E18(),
                        irm_contract.functions.JUMP_MULTIPLIER_E18(),
                        irm_contract.functions.JUMP_UTIL_E18(),
                        oracle_contract.functions.getPrice_e36(address)
                    ]
                    
                    borrow_rate_multiplier = static_batch_calls[0].call()
                    jump_multiplier = static_batch_calls[1].call()
                    jump_util = static_batch_calls[2].call()
                    price_e36 = static_batch_calls[3].call()
                except Exception:
                    borrow_rate_multiplier = irm_contract.functions.BORR_RATE_MULTIPLIER_E18().call()
                    jump_multiplier = irm_contract.functions.JUMP_MULTIPLIER_E18().call()
                    jump_util = irm_contract.functions.JUMP_UTIL_E18().call()
                    price_e36 = oracle_contract.functions.getPrice_e36(address).call()
                
                E18 = 10 ** 18
                slope1 = (borrow_rate_multiplier / E18) * cls.SECONDS_IN_YEAR
                slope2 = (jump_multiplier / E18) * cls.SECONDS_IN_YEAR
                optimal_utilization_rate = jump_util / E18
                
                try:
                    reserve_factor_e18 = pool_contract.functions.reserveFactor_e18().call()
                    reserve_factor = reserve_factor_e18 / E18
                except Exception:
                    reserve_factor = 0
                
                pool_decimals = pool_contract.functions.decimals().call()
                adjusted_decimals = pool_decimals - 8
                price_usd = float(price_e36) / (10 ** (36 - adjusted_decimals))
                
                base_reserve_name = get_reserve_name(address)
                if not base_reserve_name:
                    base_reserve_name = f'Reserve-{address[:8]}'
                reserve_name = f"{base_reserve_name} Reserve"
                rewards_per_year = cls.get_reserve_rewards(reserve_name)
                
                # Cache static data for 1 day
                cache_manager.set_cached_data('init', 'mantle', static_cache_key, {
                    'name': reserve_name,
                    'optimal_usage_ratio': optimal_utilization_rate,
                    'variable_rate_slope1': slope1,
                    'variable_rate_slope2': slope2,
                    'token_price': price_usd,
                    'reserve_factor': reserve_factor,
                    'rewards_per_year': rewards_per_year
                }, ttl=86400)

            reserve_info = {
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
                'base_variable_borrow_rate': 0,
                'reserve_factor': reserve_factor,
                'source': cls.SOURCE,
                'network': cls.NETWORK_NAME,
                'rewards_per_year': rewards_per_year,
                'type': 'reserve'
            }
            
            return reserve_info
            
        except Exception as e:
            logging.error(f"Error processing Init reserve {fund_data.get('address')}: {str(e)}")
            return None 