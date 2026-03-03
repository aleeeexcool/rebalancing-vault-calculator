"""
Lendle Protocol Adapter V2 for Mantle network
"""
import logging
import json
import os
from typing import Dict, Optional, List, Any
from web3 import Web3
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_reserve_name
from .core.cache_manager import cache_manager

def load_abi(filename):
    """
    Load ABI from a JSON file
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    abi_path = os.path.join(current_dir, 'abi', filename)
    with open(abi_path, 'r') as file:
        return json.load(file)

# Load contract ABIs
DEFAULT_RESERVE_INTEREST_RATE_STRATEGY_ABI = load_abi('lendle_mantle_strategy_abi.json')
POOL_ADDRESSES_PROVIDER_ABI = load_abi('lendle_mantle_addresses_provider_abi.json')
PRICE_ORACLE_ABI = load_abi('lendle_mantle_price_oracle_abi.json')
POOL_DATA_PROVIDER_ABI = load_abi('lendle_mantle_pool_data_provider_abi.json')
INCENTIVE_DATA_PROVIDER_ABI = load_abi('lendle_mantle_incentive_provider_abi.json')

class LendleMantleAdapterV2(BaseProtocolAdapter):
    """
    Adapter for Lendle protocol V2 on Mantle network
    """
    SOURCE = "Lendle Mantle V2"
    PROTOCOL_NAME = "Lendle"
    NETWORK_NAME = "Mantle"
    
    # Mantle RPC URLs
    RPC_URLS = [
        "https://mantle-mainnet.public.blastapi.io"
    ]
    
    # Contract addresses
    INTEREST_RATE_STRATEGY_ADDRESS = "0x36ED726A95c50bd7e2d4220d77f695637616Bca2"
    INCENTIVE_DATA_PROVIDER_ADDRESS = "0xe012c609dB55209a2Ab994aE62e92728f8AA45fe"
    
    # Constants
    SECONDS_IN_YEAR = 31536000
    RAY = 10**27
    
    # Hardcoded reward token prices
    REWARD_TOKEN_PRICES = {
        'LEND': 0.0136,
        'MNT': 0.7577851267923226,
        'WMNT': 0.7577851267923226
    }
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from Lendle protocol V2
        """
        # Check in source field
        if fund_data.get('source') == cls.SOURCE:
            return True
            
        # Additional detection logic if needed
        return False
    
    @classmethod
    def get_web3_provider(cls):
        """
        Get Web3 provider for Mantle network with fallback
        """
        for rpc_url in cls.RPC_URLS:
            try:
                provider = Web3(Web3.HTTPProvider(rpc_url))
                if provider.is_connected():
                    return provider
            except Exception as e:
                logging.error(f"Failed to connect to RPC {rpc_url}: {e}")
                continue
        
        raise Exception("Failed to connect to any Mantle RPC")
    
    @classmethod
    def get_interest_rate_data(cls, token_address: str) -> Dict:
        """
        Get interest rate data from the strategy contract
        """
        try:
            web3 = cls.get_web3_provider()
            strategy_contract = web3.eth.contract(
                address=Web3.to_checksum_address(cls.INTEREST_RATE_STRATEGY_ADDRESS),
                abi=DEFAULT_RESERVE_INTEREST_RATE_STRATEGY_ABI
            )
            
            # Get interest rate data
            rate_data = strategy_contract.functions.getInterestRateData(
                Web3.to_checksum_address(token_address)
            ).call()
            
            return {
                'optimal_usage_ratio': rate_data[0] / cls.RAY,
                'base_variable_borrow_rate': rate_data[1] / cls.RAY,
                'variable_rate_slope1': rate_data[2] / cls.RAY,
                'variable_rate_slope2': rate_data[3] / cls.RAY
            }
        except Exception as e:
            logging.error(f"Error getting interest rate data: {e}")
            return None
    
    @classmethod
    def get_token_price(cls, token_address: str) -> float:
        """
        Get token price from the price oracle
        """
        try:
            web3 = cls.get_web3_provider()
            
            # Get strategy contract
            strategy_contract = web3.eth.contract(
                address=Web3.to_checksum_address(cls.INTEREST_RATE_STRATEGY_ADDRESS),
                abi=DEFAULT_RESERVE_INTEREST_RATE_STRATEGY_ABI
            )
            
            # Get addresses provider
            addresses_provider_address = strategy_contract.functions.ADDRESSES_PROVIDER().call()
            addresses_provider = web3.eth.contract(
                address=addresses_provider_address,
                abi=POOL_ADDRESSES_PROVIDER_ABI
            )
            
            # Get price oracle
            price_oracle_address = addresses_provider.functions.getPriceOracle().call()
            price_oracle = web3.eth.contract(
                address=price_oracle_address,
                abi=PRICE_ORACLE_ABI
            )
            
            # Get price
            price = price_oracle.functions.getAssetPrice(
                Web3.to_checksum_address(token_address)
            ).call()
            
            return float(price) / 10**8  # Assuming 8 decimals for price
        except Exception as e:
            logging.error(f"Error getting token price: {e}")
            return 0.0
    
    @classmethod
    def calculate_borrow_rate(cls, utilization: float, rate_data: Dict) -> float:
        """
        Calculate borrow rate using the piecewise function
        """
        if utilization <= rate_data['optimal_usage_ratio']:
            return rate_data['base_variable_borrow_rate'] + (rate_data['variable_rate_slope1'] * utilization)
        else:
            excess = utilization - rate_data['optimal_usage_ratio']
            return (rate_data['base_variable_borrow_rate'] + 
                   (rate_data['variable_rate_slope1'] * rate_data['optimal_usage_ratio']) +
                   (rate_data['variable_rate_slope2'] * excess))
    
    @classmethod
    def get_pool_data_provider_address(cls):
        web3 = cls.get_web3_provider()
        strategy_contract = web3.eth.contract(
            address=Web3.to_checksum_address(cls.INTEREST_RATE_STRATEGY_ADDRESS),
            abi=DEFAULT_RESERVE_INTEREST_RATE_STRATEGY_ABI
        )
        addresses_provider_address = strategy_contract.functions.ADDRESSES_PROVIDER().call()
        addresses_provider = web3.eth.contract(
            address=addresses_provider_address,
            abi=POOL_ADDRESSES_PROVIDER_ABI
        )
        return addresses_provider.functions.getPoolDataProvider().call()

    @classmethod
    def get_total_supplied(cls, token_address: str) -> float:
        web3 = cls.get_web3_provider()
        pool_data_provider_address = cls.get_pool_data_provider_address()
        pool_data_provider = web3.eth.contract(
            address=pool_data_provider_address,
            abi=POOL_DATA_PROVIDER_ABI
        )
        total_supply = pool_data_provider.functions.getATokenTotalSupply(
            Web3.to_checksum_address(token_address)
        ).call()
        return float(total_supply)

    @classmethod
    def get_total_borrowed(cls, token_address: str) -> float:
        web3 = cls.get_web3_provider()
        pool_data_provider_address = cls.get_pool_data_provider_address()
        pool_data_provider = web3.eth.contract(
            address=pool_data_provider_address,
            abi=POOL_DATA_PROVIDER_ABI
        )
        total_borrowed = pool_data_provider.functions.getTotalDebt(
            Web3.to_checksum_address(token_address)
        ).call()
        return float(total_borrowed)

    @classmethod
    def get_all_reserves_tokens(cls):
        web3 = cls.get_web3_provider()
        pool_data_provider_address = cls.get_pool_data_provider_address()
        pool_data_provider = web3.eth.contract(
            address=pool_data_provider_address,
            abi=POOL_DATA_PROVIDER_ABI
        )
        return pool_data_provider.functions.getAllReservesTokens().call()
    
    @classmethod
    def get_addresses_provider_address(cls):
        web3 = cls.get_web3_provider()
        strategy_contract = web3.eth.contract(
            address=Web3.to_checksum_address(cls.INTEREST_RATE_STRATEGY_ADDRESS),
            abi=DEFAULT_RESERVE_INTEREST_RATE_STRATEGY_ABI
        )
        return strategy_contract.functions.ADDRESSES_PROVIDER().call()
    
    @classmethod
    def get_reserves_incentives_data(cls) -> Dict[str, Dict]:
        """
        Get incentive data for all reserves
        
        Returns:
            Dict[str, Dict]: Dictionary with token addresses as keys and incentive data as values
        """
        try:
            # Try to get cached incentives data (24 hours TTL)
            cached_incentives = cache_manager.get_cached_data('lendle_isolated_incentives', 'mantle', 'all_reserves_incentives')
            if cached_incentives:
                return cached_incentives.get('incentives', {})
            
            # Fetch fresh incentives data
            web3 = cls.get_web3_provider()
            addresses_provider_address = cls.get_addresses_provider_address()
            
            # Initialize the incentive data provider contract
            incentive_provider = web3.eth.contract(
                address=Web3.to_checksum_address(cls.INCENTIVE_DATA_PROVIDER_ADDRESS),
                abi=INCENTIVE_DATA_PROVIDER_ABI
            )
            
            # Get incentives data
            incentives_data = incentive_provider.functions.getReservesIncentivesData(
                addresses_provider_address
            ).call()
            
            # Process and format the data
            result = {}
            for item in incentives_data:
                underlying_asset = item[0].lower()
                a_incentive_data = item[1]
                v_incentive_data = item[2]
                
                # Process aToken rewards info
                a_rewards_info = []
                if a_incentive_data[2] and len(a_incentive_data[2]) > 0:
                    for reward_info in a_incentive_data[2]:
                        a_rewards_info.append({
                            'rewardTokenSymbol': reward_info[0],
                            'rewardTokenAddress': reward_info[1],
                            'rewardOracleAddress': reward_info[2],
                            'emissionPerSecond': int(reward_info[3]),
                            'incentivesLastUpdateTimestamp': int(reward_info[4]),
                            'tokenIncentivesIndex': int(reward_info[5]),
                            'emissionEndTimestamp': int(reward_info[6]),
                            'rewardPriceFeed': int(reward_info[7]),
                            'rewardTokenDecimals': int(reward_info[8]),
                            'precision': int(reward_info[9]),
                            'priceFeedDecimals': int(reward_info[10])
                        })
                
                # Process vToken rewards info
                v_rewards_info = []
                if v_incentive_data[2] and len(v_incentive_data[2]) > 0:
                    for reward_info in v_incentive_data[2]:
                        v_rewards_info.append({
                            'rewardTokenSymbol': reward_info[0],
                            'rewardTokenAddress': reward_info[1],
                            'rewardOracleAddress': reward_info[2],
                            'emissionPerSecond': int(reward_info[3]),
                            'incentivesLastUpdateTimestamp': int(reward_info[4]),
                            'tokenIncentivesIndex': int(reward_info[5]),
                            'emissionEndTimestamp': int(reward_info[6]),
                            'rewardPriceFeed': int(reward_info[7]),
                            'rewardTokenDecimals': int(reward_info[8]),
                            'precision': int(reward_info[9]),
                            'priceFeedDecimals': int(reward_info[10])
                        })
                
                result[underlying_asset] = {
                    'underlyingAsset': item[0],
                    'aIncentiveData': {
                        'tokenAddress': a_incentive_data[0],
                        'incentiveControllerAddress': a_incentive_data[1],
                        'rewards': a_rewards_info
                    },
                    'vIncentiveData': {
                        'tokenAddress': v_incentive_data[0],
                        'incentiveControllerAddress': v_incentive_data[1],
                        'rewards': v_rewards_info
                    }
                }
            
            # Cache incentives data for 24 hours
            cache_manager.set_cached_data('lendle_isolated_incentives', 'mantle', 'all_reserves_incentives', {
                'incentives': result
            }, ttl=86400)
            
            return result
        except Exception as e:
            logging.error(f"Error getting incentives data: {e}")
            return {}
    
    @classmethod
    def calculate_incentive_apr(cls, incentive_data: Dict[str, Any], total_supplied: float, total_borrowed: float, token_price: float) -> Dict[str, float]:
        """
        Calculate APR from incentives data
        
        Args:
            incentive_data: Incentive data for a specific token
            total_supplied: Total supplied amount
            total_borrowed: Total borrowed amount
            token_price: Token price in USD
            
        Returns:
            Dict[str, float]: Dictionary with supply and borrow APRs as decimals (0-1 range)
        """
        try:
            supply_apr = 0.0
            borrow_apr = 0.0
            
            # Calculate supply APR from aToken incentives
            if incentive_data.get('aIncentiveData', {}).get('rewards'):
                for reward in incentive_data['aIncentiveData']['rewards']:
                    # Skip if emission is 0 or ended
                    if reward['emissionPerSecond'] <= 0 or (reward['emissionEndTimestamp'] > 0 and 
                       reward['emissionEndTimestamp'] < reward['incentivesLastUpdateTimestamp']):
                        continue
                    
                    # Convert emissionPerSecond to right decimals
                    emission_per_second = reward['emissionPerSecond'] / (10 ** reward['rewardTokenDecimals'])
                    # Convert reward price feed to right decimals
                    reward_price_feed = reward['rewardPriceFeed'] / (10 ** reward['priceFeedDecimals'])
                    
                    # Get hardcoded reward token price
                    reward_token_price = cls.get_reward_token_price(reward['rewardTokenSymbol'])
                    
                    # Calculate yearly rewards in USD
                    yearly_rewards_usd = emission_per_second * cls.SECONDS_IN_YEAR * reward_price_feed * reward_token_price
                    
                    # Calculate total supply in USD
                    total_supplied_usd = total_supplied * token_price
                    
                    if total_supplied_usd > 0:
                        # Calculate annual rewards rate (APR not APY)
                        # This is a simple linear rate, not compounded
                        supply_apr += yearly_rewards_usd / total_supplied_usd
            
            # Calculate borrow APR from vToken incentives
            if incentive_data.get('vIncentiveData', {}).get('rewards'):
                for reward in incentive_data['vIncentiveData']['rewards']:
                    # Skip if emission is 0 or ended
                    if reward['emissionPerSecond'] <= 0 or (reward['emissionEndTimestamp'] > 0 and 
                       reward['emissionEndTimestamp'] < reward['incentivesLastUpdateTimestamp']):
                        continue
                    
                    # Convert emissionPerSecond to right decimals
                    emission_per_second = reward['emissionPerSecond'] / (10 ** reward['rewardTokenDecimals'])
                    # Convert reward price feed to right decimals
                    reward_price_feed = reward['rewardPriceFeed'] / (10 ** reward['priceFeedDecimals'])
                    
                    # Get hardcoded reward token price
                    reward_token_price = cls.get_reward_token_price(reward['rewardTokenSymbol'])
                    
                    # Calculate yearly rewards in USD
                    yearly_rewards_usd = emission_per_second * cls.SECONDS_IN_YEAR * reward_price_feed * reward_token_price
                    
                    # Calculate total borrowed in USD
                    total_borrowed_usd = total_borrowed * token_price
                    
                    if total_borrowed_usd > 0:
                        # Calculate annual rewards rate (APR not APY)
                        # This is a simple linear rate, not compounded
                        borrow_apr += yearly_rewards_usd / total_borrowed_usd
            
            return {
                'supply_apr': supply_apr,
                'borrow_apr': borrow_apr
            }
        except Exception as e:
            logging.error(f"Error calculating incentive APR: {e}")
            return {'supply_apr': 0.0, 'borrow_apr': 0.0}

    @classmethod
    def calculate_reserve_apy(cls, our_supply, investment) -> tuple:
        try:
            # Get total borrowed and supplied from investment data, already scaled correctly
            total_borrowed = investment.get('total_borrowed', 0)
            total_supplied = investment.get('total_supplied', 0)
            
            # Calculate utilization rate including our supply
            utilization = total_borrowed / (total_supplied + our_supply) if (total_supplied + our_supply) > 0 else 0
            
            if utilization == 0:
                return 0, 0, 0, 0, 0, 0

            # Get rate parameters from investment data
            base_rate = investment.get('base_variable_borrow_rate', 0)
            slope1 = investment.get('variable_rate_slope1', 0)
            slope2 = investment.get('variable_rate_slope2', 0)
            optimal_ratio = investment.get('optimal_usage_ratio', 0)
            reserve_factor = investment.get('reserve_factor', 0.1)

            # Calculate borrow rate based on utilization
            if utilization > optimal_ratio:
                excess_borrow_usage_ratio = (utilization - optimal_ratio) / (1 - optimal_ratio)
                borrow_rate = base_rate + slope1 + (slope2 * excess_borrow_usage_ratio)
            else:
                borrow_rate = base_rate + (slope1 * utilization / optimal_ratio)

            # Calculate supply rate (liquidity rate)
            supply_rate = borrow_rate * utilization * (1 - reserve_factor)

            # Calculate APY directly (this is what was previously calculated as APR)
            reserve_apy = supply_rate
            # Convert APY to APR
            reserve_apr = 365 * ((1 + reserve_apy/100) ** (1/365) - 1) * 100
            
            # Calculate rewards APY from incentives, adjusted for our supply
            token_price = investment.get('token_price', 0)
            incentives_data = investment.get('incentives_data', {})
            
            # Recalculate rewards APY taking into account our supply
            rewards_apy = 0.0
            
            if incentives_data and token_price > 0:
                if incentives_data.get('aIncentiveData', {}).get('rewards'):
                    for reward in incentives_data['aIncentiveData']['rewards']:
                        # Skip if emission is 0 or ended
                        if reward.get('emissionPerSecond', 0) <= 0 or (reward.get('emissionEndTimestamp', 0) > 0 and 
                           reward.get('emissionEndTimestamp', 0) < reward.get('incentivesLastUpdateTimestamp', 0)):
                            continue
                        
                        # Convert emissionPerSecond to right decimals
                        emission_per_second = reward.get('emissionPerSecond', 0) / (10 ** reward.get('rewardTokenDecimals', 18))
                        # Convert reward price feed to right decimals
                        reward_price_feed = reward.get('rewardPriceFeed', 0) / (10 ** reward.get('priceFeedDecimals', 8))
                        
                        # Get hardcoded reward token price
                        reward_token_price = cls.get_reward_token_price(reward['rewardTokenSymbol'])
                        
                        # Calculate yearly rewards in USD
                        yearly_rewards_usd = emission_per_second * cls.SECONDS_IN_YEAR * reward_price_feed * reward_token_price
                        
                        # Calculate total supply in USD including our supply
                        total_supplied_usd = (total_supplied + our_supply) * token_price
                        
                        if total_supplied_usd > 0:
                            # Calculate APY directly (this is what we previously calculated as APR)
                            rewards_apy += yearly_rewards_usd / total_supplied_usd
            else:
                # Fallback to pre-calculated rewards if incentives data not available
                rewards_apy = investment.get('rewards_supply_apr', 0)
                
                # If we have our own supply, recalculate the rewards APY
                if rewards_apy > 0 and our_supply > 0 and total_supplied > 0 and token_price > 0:
                    # Calculate total rewards in USD (based on the existing APY)
                    total_rewards_usd = rewards_apy * total_supplied * token_price
                    
                    # Recalculate APY based on new total supply including our supply
                    rewards_apy = total_rewards_usd / ((total_supplied + our_supply) * token_price)
            
            # Convert APY to APR (with proper scaling for percentage values)
            rewards_apr = 365 * ((1 + rewards_apy/100) ** (1/365) - 1) * 100 if rewards_apy > 0 else 0
            
            # Calculate total APY/APR
            total_apy = reserve_apy + rewards_apy
            total_apr = reserve_apr + rewards_apr

            return reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr
        except Exception as e:
            logging.error(f"Error calculating reserve APY: {e}")
            return 0, 0, 0, 0, 0, 0
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch and process Lendle reserve data with caching
        Uses caching for static data to reduce RPC calls
        """
        try:
            token_address = fund_data['address']
            token_address = Web3.to_checksum_address(token_address)
            
            # Try to get cached static data (1 day TTL)
            static_cache_key = f"static_{token_address}"
            cached_static = cache_manager.get_cached_data('lendle_isolated', 'mantle', static_cache_key)
            
            # Try to get cached dynamic data (10 min TTL)
            dynamic_cache_key = f"dynamic_{token_address}"
            cached_dynamic = cache_manager.get_cached_data('lendle_isolated', 'mantle', dynamic_cache_key)
            
            # Get dynamic data (total_borrowed, total_supplied)
            if cached_dynamic:
                total_borrowed = cached_dynamic['total_borrowed']
                total_supplied = cached_dynamic['total_supplied']
            else:
                total_borrowed = cls.get_total_borrowed(token_address) / 1_000_000
                total_supplied = cls.get_total_supplied(token_address) / 1_000_000
                
                # Cache dynamic data for 10 minutes
                cache_manager.set_cached_data('lendle_isolated', 'mantle', dynamic_cache_key, {
                    'total_borrowed': total_borrowed,
                    'total_supplied': total_supplied
                }, ttl=600)
            
            # Calculate utilization rate from cached/fresh data
            utilization = total_borrowed / total_supplied if total_supplied > 0 else 0
            
            # Get incentives data (cached separately for 6 hours)
            incentives_data = cls.get_reserves_incentives_data()
            token_lower = token_address.lower()
            incentive_data = incentives_data.get(token_lower, {})
            
            # Get static data
            if cached_static:
                reserve_name = cached_static['name']
                rate_data = {
                    'optimal_usage_ratio': cached_static['optimal_usage_ratio'],
                    'variable_rate_slope1': cached_static['variable_rate_slope1'],
                    'variable_rate_slope2': cached_static['variable_rate_slope2'],
                    'base_variable_borrow_rate': cached_static['base_variable_borrow_rate']
                }
                token_price = cached_static['token_price']
                reserve_factor = cached_static['reserve_factor']
            else:
                # Fetch fresh static data
                rate_data = cls.get_interest_rate_data(token_address)
                if not rate_data:
                    return None
                
                token_price = cls.get_token_price(token_address)
                
                web3 = cls.get_web3_provider()
                pool_data_provider_address = cls.get_pool_data_provider_address()
                pool_data_provider = web3.eth.contract(
                    address=pool_data_provider_address,
                    abi=POOL_DATA_PROVIDER_ABI
                )
                reserve_config = pool_data_provider.functions.getReserveConfigurationData(token_address).call()
                reserve_factor = reserve_config[4] / 1e4 if len(reserve_config) > 4 else 0.1
                
                base_reserve_name = get_reserve_name(token_address)
                if not base_reserve_name:
                    base_reserve_name = f'Reserve-{token_address[:8]}'
                reserve_name = f"{base_reserve_name} Reserve"
                
                # Cache static data for 1 day
                cache_manager.set_cached_data('lendle_isolated', 'mantle', static_cache_key, {
                    'name': reserve_name,
                    'optimal_usage_ratio': rate_data['optimal_usage_ratio'],
                    'variable_rate_slope1': rate_data['variable_rate_slope1'],
                    'variable_rate_slope2': rate_data['variable_rate_slope2'],
                    'base_variable_borrow_rate': rate_data['base_variable_borrow_rate'],
                    'token_price': token_price,
                    'reserve_factor': reserve_factor
                }, ttl=86400)
            
            # Calculate incentive APRs with current data
            incentive_aprs = cls.calculate_incentive_apr(
                incentive_data, 
                total_supplied, 
                total_borrowed, 
                token_price
            )
            
            # Create the reserve info dictionary
            reserve_info = {
                'name': reserve_name,
                'protocol': cls.PROTOCOL_NAME,
                'total_borrowed': total_borrowed,
                'total_supplied': total_supplied,
                'utilization_rate': utilization,
                'optimal_usage_ratio': rate_data['optimal_usage_ratio'],
                'variable_rate_slope1': rate_data['variable_rate_slope1'],
                'variable_rate_slope2': rate_data['variable_rate_slope2'],
                'token_price': token_price,
                'fee_percentage': 0,
                'base_variable_borrow_rate': rate_data['base_variable_borrow_rate'],
                'reserve_factor': reserve_factor,
                'source': cls.SOURCE,
                'network': cls.NETWORK_NAME,
                'rewards_per_year': 0,
                'rewards_supply_apr': incentive_aprs['supply_apr'],
                'rewards_borrow_apr': incentive_aprs['borrow_apr'],
                'incentives_data': incentive_data,
                'type': 'reserve'
            }

            return reserve_info
        except Exception as e:
            logging.error(f"Error processing Lendle reserve {fund_data.get('address')}: {str(e)}")
            return None

    @classmethod
    def get_reward_token_price(cls, reward_token_symbol: str) -> float:
        """
        Get hardcoded price for reward tokens
        """
        return cls.REWARD_TOKEN_PRICES.get(reward_token_symbol, 0.0) 