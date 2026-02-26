"""
Merchant Moe Protocol Adapter for liquidity pools on Mantle network
"""
import logging
import requests
import json
import os
from web3 import Web3
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_token_price

class MerchantMoeAdapter(BaseProtocolAdapter):
    """
    Adapter for Merchant Moe protocol (liquidity pools) on Mantle network
    """
    SOURCE = "Merchant Moe Mantle"
    PROTOCOL_NAME = "Merchant Moe"
    NETWORK_NAME = "Mantle"
    MOE_TOKEN_ADDRESS = "0x4515A45337F461A11Ff0FE8aBF3c606AE5dC00c9"
    MOE_CONTRACT_ADDRESS = "0x93185784e04D8B5a0aDe98d80C48AA06B9689Ee8"
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is a Merchant Moe pool
        """
        # Check in source field
        if fund_data.get('source') == cls.SOURCE:
            return True
            
        # If type is pool, we assume it's a Merchant Moe pool
        if fund_data.get('type') == 'pool':
            return True
            
        return False
    
    @classmethod
    def calculate_pool_apr(cls, daily_fee, pool_distribution, protocol_fee=0):
        """
        Calculate pool APR
        
        Args:
            daily_fee: Daily fee in USD
            pool_distribution: Total pool distribution in USD
            protocol_fee: Protocol fee percentage (0-1)
            
        Returns:
            float: Pool APR
        """
        return (daily_fee * 365 * (1 - protocol_fee)) / pool_distribution
    
    @classmethod
    def calculate_rewards_apr(cls, reward_per_day, reward_token_price, pool_distribution):
        """
        Calculate rewards APR
        
        Args:
            reward_per_day: Daily reward amount
            reward_token_price: Price of reward token in USD
            pool_distribution: Total pool distribution in USD
            
        Returns:
            float: Rewards APR
        """
        return (reward_per_day * reward_token_price * 365) / pool_distribution
    
    @classmethod
    def calculate_pool_apy(cls, apr):
        """
        Convert APR to APY
        
        Args:
            apr: Annual Percentage Rate
            
        Returns:
            float: Annual Percentage Yield
        """
        return (1 + apr/365)**365 - 1
    
    @classmethod
    def calculate_pool_apr_apy(cls, our_supply, pool_data):
        """
        Calculate pool APR/APY with all metrics
        
        Args:
            our_supply: Amount we're planning to supply
            pool_data: Pool data dictionary
            
        Returns:
            tuple: (base_apy, rewards_apy, total_apy, base_apr, rewards_apr, total_apr)
        """
        # Total distribution after adding our_supply
        total_distribution = pool_data.get('pool_distribution', 0) + our_supply
        
        # Get protocol fee (default 0.0 or 0% if not specified)
        protocol_fee = pool_data.get('protocol_fee', 0.0)
        
        # Calculate Pool APR and convert to APY
        daily_fee = pool_data.get('daily_fee', 0)
        base_apr = cls.calculate_pool_apr(daily_fee, total_distribution, protocol_fee) if total_distribution > 0 else 0
        base_apy = cls.calculate_pool_apy(base_apr)
        
        # Get reward values
        reward_per_day = pool_data.get('reward_per_day', 0)
        reward_token_price = pool_data.get('reward_token_price', 0)
        
        # Calculate Rewards APR and convert to APY
        rewards_apr = 0.0
        rewards_apy = 0.0
        if reward_per_day > 0 and reward_token_price > 0 and total_distribution > 0:
            rewards_apr = cls.calculate_rewards_apr(reward_per_day, reward_token_price, total_distribution)
            rewards_apy = cls.calculate_pool_apy(rewards_apr)
        
        # Total APR and APY
        total_apr = base_apr + rewards_apr
        total_apy = base_apy + rewards_apy
        
        return base_apy, rewards_apy, total_apy, base_apr, rewards_apr, total_apr
    
    @classmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        """
        MerchantMoe doesn't have borrowing, implementing abstract method
        """
        return 0
    
    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        MerchantMoe doesn't have reserves, implementing abstract method
        
        Returns:
            tuple: (reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr)
        """
        return 0, 0, 0, 0, 0, 0
    
    @classmethod
    def get_moe_value(cls, pair_address, user_address):
        """
        Calculate MOE rewards value for a pool
        
        Args:
            pair_address: Pool contract address
            user_address: User wallet address
            
        Returns:
            float: MOE rewards value per day or None if error
        """
        try:
            # Initialize Web3 with hardcoded Mantle RPC
            w3 = Web3(Web3.HTTPProvider('https://rpc.mantle.xyz'))

            # Load ABI
            with open('protocols/abi/abi.json', 'r') as f:
                contract_abi = json.load(f)

            # Create contract instance
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(cls.MOE_CONTRACT_ADDRESS), 
                abi=contract_abi
            )

            # Get the function object
            get_hooks_data = contract.functions.getHooksData
            
            # Call the function with parameters
            result = get_hooks_data(
                Web3.to_checksum_address(pair_address),
                Web3.to_checksum_address(user_address),
                [0]
            ).call()
            
            # Extract the specific value from the first tuple
            if isinstance(result, list) and len(result) > 0:
                first_tuple = result[0]
                if isinstance(first_tuple, tuple) and len(first_tuple) >= 5:
                    value = first_tuple[4]  # Get the value at index 4
                    # Perform calculations: (value * seconds_in_day) / 0.6316 / 10**18
                    seconds_in_day = 86400
                    calculated_value = (value * seconds_in_day) * 0.6316 / (10**18)
                    return calculated_value
            return None
        except Exception as e:
            logging.error(f"Error calculating MOE value: {e}")
            return None
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        Fetch and process Merchant Moe pool data
        """
        try:
            address = fund_data['address']
            
            # Get pool data from API
            response = requests.get(f"https://barn.merchantmoe.com/v1/lb/pools/mantle/{address}")
            if response.status_code != 200:
                logging.error(f"Failed to fetch pool data: {response.text}")
                return None
                
            pool_data = response.json()
            
            # Get MOE rewards
            reward_per_day = cls.get_moe_value(address, wallet_address)
            
            # Get MOE token price
            moe_price = get_token_price(cls.MOE_TOKEN_ADDRESS)
            
            pool_info = {
                'name': pool_data.get('name', f'Pool-{address[:8]}'),
                'daily_fee': pool_data.get('feesUsd', 0),
                'pool_distribution': pool_data.get('liquidityUsd', 0),
                'reward_per_day': reward_per_day if reward_per_day is not None else 0,
                'reward_token_price': moe_price,
                'source': cls.SOURCE,
                'network': cls.NETWORK_NAME,
                'protocol': cls.PROTOCOL_NAME,
                'type': 'pool'
            }
            return pool_info
            
        except Exception as e:
            logging.error(f"Error processing pool {fund_data.get('address')}: {str(e)}")
            return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        MerchantMoe doesn't have reserves in this context, only pools
        """
        return None

