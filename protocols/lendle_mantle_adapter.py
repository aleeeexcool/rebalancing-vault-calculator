"""
Lendle Protocol Adapter for Mantle network
"""
import logging
import json
import os
import time
from typing import Dict, Optional, List
from decimal import Decimal
from web3 import Web3
import requests
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_reserve_name
from .core.cache_manager import cache_manager

def load_abi(filename):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    abi_path = os.path.join(current_dir, 'abi', filename)
    with open(abi_path, 'r') as file:
        return json.load(file)

LENDING_POOL_ABI = load_abi('lendle_lending_pool_abi.json')
ATOKEN_ABI = load_abi('lendle_atoken_abi.json')
INTEREST_RATE_STRATEGY_ABI = load_abi('lendle_interest_rate_strategy_abi.json')
DATA_PROVIDER_ABI = load_abi('lendle_data_provider_abi.json')

class LendleMantleAdapter(BaseProtocolAdapter):
    SOURCE = "Lendle Mantle"
    PROTOCOL_NAME = "Lendle"
    NETWORK_NAME = "Mantle"
    # LEND_TOKEN_ADDRESS = "0x25356aeca4210ef7553140edb9b8026089e49396"
    
    RPC_URLS = ["https://rpc.mantle.xyz"]

    LENDING_POOL_ADDRESS = "0xCFa5aE7c2CE8Fadc6426C1ff872cA45378Fb7cF3"
    DATA_PROVIDER_ADDRESS = "0x552b9e4bae485C4B7F540777d7D25614CdB84773"
    
    # LENDLE_RESERVE_REWARDS: Dict[str, float] = {
    #     'AUSD Reserve': 1565614.0580529927,
    #     'USDC Reserve': 670977.4534512826,
    #     'USDT Reserve': 670977.4534512826,
    #     'WBTC Reserve': 2236.5915115042753,
    #     'ETH Reserve': 617299.25717518,
    #     'WMNT Reserve': 335488.7267256413,
    #     'mETH Reserve': 111829.57557521376,
    #     'USDE Reserve': 335488.7267256413,
    #     'FBTC Reserve': 514416.0476459833,
    #     'cmETH Reserve': 111829.57557521376,
    #     'sUSDe Reserve': 2236.5915115042753
    # }
    
    @classmethod
    def get_web3_provider(cls):
        # Cached provider lookup
        cached_provider_data = cache_manager.get_cached_data('lendle', 'mantle', 'web3_provider')
        if cached_provider_data:
            try:
                cached_rpc_url = cached_provider_data.get('rpc_url')
                if cached_rpc_url:
                    return Web3(Web3.HTTPProvider(cached_rpc_url))
            except Exception:
                cache_manager.invalidate_cache('lendle', 'mantle', 'web3_provider')
        
        # Find working RPC
        for rpc_url in cls.RPC_URLS:
            try:
                provider = Web3(Web3.HTTPProvider(rpc_url))
                if provider.is_connected():
                    cache_manager.set_cached_data('lendle', 'mantle', 'web3_provider', {
                        'rpc_url': rpc_url
                    }, ttl=900)
                    return provider
            except Exception:
                continue
        
        raise Exception("Failed to connect to any Mantle RPC")
    
    @classmethod
    def detect_protocol(cls, fund_data):
        return fund_data.get('source') == cls.SOURCE
    
    # @classmethod
    # def get_lend_price(cls) -> float:
    #     cached_price = cache_manager.get_cached_data('lendle', 'mantle', 'lend_price')
    #     if cached_price:
    #         return cached_price.get('price', 0)
        
    #     try:
    #         response = requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{cls.LEND_TOKEN_ADDRESS}')
    #         if response.status_code == 200:
    #             data = response.json()
    #             if data.get('pairs') and len(data['pairs']) > 0:
    #                 price = float(data['pairs'][0].get('priceUsd', 0))
    #                 cache_manager.set_cached_data('lendle', 'mantle', 'lend_price', {
    #                     'price': price
    #                 }, ttl=3600)
    #                 return price
    #     except Exception:
    #         pass
    #     return 0
    
    @classmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        if utilization <= optimal_usage_ratio:
            return base_variable_borrow_rate + (variable_rate_slope1 * utilization) / optimal_usage_ratio
        else:
            excess = (utilization - optimal_usage_ratio) / (1 - optimal_usage_ratio)
            return base_variable_borrow_rate + (variable_rate_slope1 * utilization) + (variable_rate_slope2 * excess)
    
    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
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
        
        reserve_factor = reserve_data.get('reserve_factor', reserve_data.get('fee_percentage', 0))
        reserve_apy = borrow_apy * utilization * (1 - reserve_factor)
        
        rewards_apy = 0.0
        if reserve_data.get('rewards_per_year', 0) > 0 and reserve_data.get('token_price', 0) > 0:
            rewards_apr = reserve_data['rewards_per_year'] / ((total_supplied + our_supply) * reserve_data['token_price'])
            rewards_apy = (1 + rewards_apr/365)**365 - 1
        
        total_apy = reserve_apy + rewards_apy
        reserve_apr = 365 * ((1 + reserve_apy) ** (1/365) - 1) if reserve_apy > 0 else 0
        rewards_apr = 365 * ((1 + rewards_apy) ** (1/365) - 1) if rewards_apy > 0 else 0
        total_apr = reserve_apr + rewards_apr
        
        return reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr
            
    # @classmethod
    # def get_reserve_rewards(cls, reserve_name: str) -> float:
    #     base_rewards = cls.LENDLE_RESERVE_REWARDS.get(reserve_name, 0)
    #     if base_rewards > 0:
    #         return base_rewards * cls.get_lend_price()
    #     return 0
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        start_time = time.time()
        try:
            address = Web3.to_checksum_address(fund_data['address'])
            
            # Cache lookups
            static_cache_key = f"static_{address}"
            cached_static = cache_manager.get_cached_data('lendle', 'mantle', static_cache_key)
            
            dynamic_cache_key = f"dynamic_{address}"
            cached_dynamic = cache_manager.get_cached_data('lendle', 'mantle', dynamic_cache_key)
            
            contracts_cache_key = f"contracts_{address}"
            cached_contracts = cache_manager.get_cached_data('lendle', 'mantle', contracts_cache_key)
            
            web3 = cls.get_web3_provider()
            lending_pool = web3.eth.contract(address=cls.LENDING_POOL_ADDRESS, abi=LENDING_POOL_ABI)
            
            # Contract addresses block
            if cached_contracts:
                a_token_address = cached_contracts['a_token_address']
                variable_debt_token_address = cached_contracts['variable_debt_token_address']
                interest_rate_strategy_address = cached_contracts['interest_rate_strategy_address']
            else:
                reserve_data = lending_pool.functions.getReserveData(address).call()
                a_token_address = reserve_data[7]
                variable_debt_token_address = reserve_data[9]
                interest_rate_strategy_address = reserve_data[10]
                
                cache_manager.set_cached_data('lendle', 'mantle', contracts_cache_key, {
                    'a_token_address': a_token_address,
                    'variable_debt_token_address': variable_debt_token_address,
                    'interest_rate_strategy_address': interest_rate_strategy_address
                }, ttl=86400)
            
            # Dynamic data block
            if cached_dynamic:
                formatted_total_asset = cached_dynamic['total_supplied']
                formatted_total_debt = cached_dynamic['total_borrowed']
            else:
                a_token = web3.eth.contract(address=a_token_address, abi=ATOKEN_ABI)
                variable_debt_token = web3.eth.contract(address=variable_debt_token_address, abi=ATOKEN_ABI)
                
                # Batched RPC calls
                try:
                    batch_calls = [
                        a_token.functions.decimals(),
                        a_token.functions.totalSupply(),
                        variable_debt_token.functions.totalSupply()
                    ]
                    
                    a_token_decimals = batch_calls[0].call()
                    a_token_balance = batch_calls[1].call()
                    variable_debt_token_balance = batch_calls[2].call()
                except Exception:
                    a_token_decimals = a_token.functions.decimals().call()
                    a_token_balance = a_token.functions.totalSupply().call()
                    variable_debt_token_balance = variable_debt_token.functions.totalSupply().call()
                
                formatted_total_asset = float(web3.from_wei(a_token_balance, 'ether' if a_token_decimals == 18 else 'lovelace'))
                formatted_total_debt = float(web3.from_wei(variable_debt_token_balance, 'ether' if a_token_decimals == 18 else 'lovelace'))
                
                cache_manager.set_cached_data('lendle', 'mantle', dynamic_cache_key, {
                    'total_supplied': formatted_total_asset,
                    'total_borrowed': formatted_total_debt
                }, ttl=600)
            
            utilization_rate = formatted_total_debt / formatted_total_asset if formatted_total_asset > 0 else 0
            
            # Static data block
            if cached_static:
                reserve_name = cached_static['name']
                formatted_optimal_utilization_rate = cached_static['optimal_usage_ratio']
                formatted_slope1 = cached_static['variable_rate_slope1']
                formatted_slope2 = cached_static['variable_rate_slope2']
                formatted_price_usd = cached_static['token_price']
                formatted_reserve_factor = cached_static['reserve_factor']
                rewards_per_year = cached_static['rewards_per_year']
            else:
                data_provider = web3.eth.contract(address=cls.DATA_PROVIDER_ADDRESS, abi=DATA_PROVIDER_ABI)
                interest_rate_strategy = web3.eth.contract(address=interest_rate_strategy_address, abi=INTEREST_RATE_STRATEGY_ABI)
                a_token = web3.eth.contract(address=a_token_address, abi=ATOKEN_ABI)
                
                # Batched static calls
                try:
                    static_batch_calls = [
                        a_token.functions.getAssetPrice(),
                        interest_rate_strategy.functions.OPTIMAL_UTILIZATION_RATE(),
                        interest_rate_strategy.functions.variableRateSlope1(),
                        interest_rate_strategy.functions.variableRateSlope2(),
                        data_provider.functions.getReserveConfigurationData(address)
                    ]
                    
                    price_usd = static_batch_calls[0].call()
                    optimal_utilization_rate = static_batch_calls[1].call()
                    slope1 = static_batch_calls[2].call()
                    slope2 = static_batch_calls[3].call()
                    reserve_config = static_batch_calls[4].call()
                    reserve_factor = reserve_config[4]
                except Exception:
                    price_usd = a_token.functions.getAssetPrice().call()
                    optimal_utilization_rate = interest_rate_strategy.functions.OPTIMAL_UTILIZATION_RATE().call()
                    slope1 = interest_rate_strategy.functions.variableRateSlope1().call()
                    slope2 = interest_rate_strategy.functions.variableRateSlope2().call()
                    reserve_config = data_provider.functions.getReserveConfigurationData(address).call()
                    reserve_factor = reserve_config[4]
                
                formatted_price_usd = float(web3.from_wei(price_usd, 'ether'))
                formatted_optimal_utilization_rate = float(web3.from_wei(optimal_utilization_rate, 'gwei')) / 10**18
                formatted_slope1 = float(web3.from_wei(slope1, 'gwei')) / 10**18
                formatted_slope2 = float(web3.from_wei(slope2, 'gwei')) / 10**18
                formatted_reserve_factor = float(reserve_factor) / 10**4
                
                base_reserve_name = get_reserve_name(address)
                if not base_reserve_name:
                    base_reserve_name = f'Reserve-{address[:8]}'
                reserve_name = f"{base_reserve_name} Reserve"
                # rewards_per_year = cls.get_reserve_rewards(reserve_name)
                rewards_per_year = 0
                
                cache_manager.set_cached_data('lendle', 'mantle', static_cache_key, {
                    'name': reserve_name,
                    'optimal_usage_ratio': formatted_optimal_utilization_rate,
                    'variable_rate_slope1': formatted_slope1,
                    'variable_rate_slope2': formatted_slope2,
                    'token_price': formatted_price_usd,
                    'reserve_factor': formatted_reserve_factor,
                    'rewards_per_year': rewards_per_year
                }, ttl=86400)

            return {
                'name': reserve_name,
                'protocol': cls.PROTOCOL_NAME,
                'total_borrowed': formatted_total_debt,
                'total_supplied': formatted_total_asset,
                'utilization_rate': utilization_rate,
                'optimal_usage_ratio': formatted_optimal_utilization_rate,
                'variable_rate_slope1': formatted_slope1,
                'variable_rate_slope2': formatted_slope2,
                'token_price': formatted_price_usd,
                'fee_percentage': 0,
                'base_variable_borrow_rate': 0,
                'reserve_factor': formatted_reserve_factor,
                'source': cls.SOURCE,
                'network': cls.NETWORK_NAME,
                'rewards_per_year': rewards_per_year
            }
            
        except Exception as e:
            total_time = time.time() - start_time
            logging.error(f"Lendle error after {total_time:.3f}s: {str(e)}")
            return None 