"""
Lendle Protocol Adapter V3 for Mantle network
This adapter receives aToken address and extracts underlying token address from it
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
PRICE_ORACLE_ABI = load_abi('lendle_mantle_price_oracle_abi.json')
POOL_DATA_PROVIDER_ABI = load_abi('lendle_mantle_pool_data_provider_abi.json')
INCENTIVE_DATA_PROVIDER_ABI = load_abi('lendle_mantle_incentive_provider_abi.json')

class HypurrFiHyperEvmAdapter(BaseProtocolAdapter):
    """
    Adapter for HypurrFi protocol on HyperEVM network
    This version receives aToken address and extracts underlying token address
    """
    SOURCE = "Aave Isolated HyperEVM"
    PROTOCOL_NAME = "HypurrFi"
    NETWORK_NAME = "HyperEVM"
    
    # Mantle RPC URLs
    RPC_URLS = [
        "https://rpc.hyperliquid.xyz/evm"
    ]
    
    # Contract addresses
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
    
    # Load isolated markets data from JSON file
    _ISOLATED_MARKETS_DATA = None
    _RESERVE_LOOKUP = None  # Lookup dictionary for fast access by aToken address
    
    @classmethod
    def _load_isolated_markets_data(cls):
        """
        Load isolated markets data from JSON file and create lookup dictionary
        
        Returns:
            Dict: Isolated markets data
        """
        if cls._ISOLATED_MARKETS_DATA is not None:
            return cls._ISOLATED_MARKETS_DATA
            
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(current_dir, 'hypurrfi-isolated-markets.json')
            
            with open(json_path, 'r') as file:
                cls._ISOLATED_MARKETS_DATA = json.load(file)
            
            # Create lookup dictionary for fast access by aToken address (id)
            cls._RESERVE_LOOKUP = {}
            reserves = cls._ISOLATED_MARKETS_DATA.get('reserves', [])
            
            for reserve in reserves:
                if isinstance(reserve, dict) and 'id' in reserve:
                    atoken_address = reserve['id'].lower()
                    cls._RESERVE_LOOKUP[atoken_address] = reserve
                
            logging.info(f"Loaded {len(reserves)} reserves from isolated markets JSON with lookup table")
            return cls._ISOLATED_MARKETS_DATA
        except Exception as e:
            logging.error(f"Error loading isolated markets data: {e}")
            cls._ISOLATED_MARKETS_DATA = {'reserves': []}
            cls._RESERVE_LOOKUP = {}
            return cls._ISOLATED_MARKETS_DATA
    
    @classmethod
    def _get_reserve_by_atoken(cls, atoken_address: str):
        """
        Get reserve data by aToken address using optimized lookup
        
        Args:
            atoken_address: Address of the aToken
            
        Returns:
            Dict: Reserve data or None if not found
        """
        if cls._RESERVE_LOOKUP is None:
            cls._load_isolated_markets_data()
        
        return cls._RESERVE_LOOKUP.get(atoken_address.lower())
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from Lendle protocol V3
        """
        if not fund_data or not isinstance(fund_data, dict):
            logging.warning("Invalid fund_data provided to detect_protocol")
            return False
            
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
        cached_provider_data = cache_manager.get_cached_data('lendle_isolated', 'mantle', 'web3_provider')
        if cached_provider_data:
            try:
                cached_rpc_url = cached_provider_data.get('rpc_url')
                if cached_rpc_url:
                    return Web3(Web3.HTTPProvider(cached_rpc_url))
            except Exception:
                cache_manager.invalidate_cache('lendle_isolated', 'mantle', 'web3_provider')
        
        for rpc_url in cls.RPC_URLS:
            try:
                provider = Web3(Web3.HTTPProvider(rpc_url))
                if provider.is_connected():
                    cache_manager.set_cached_data('lendle_isolated', 'mantle', 'web3_provider', {
                        'rpc_url': rpc_url
                    }, ttl=900)
                    return provider
            except Exception as e:
                logging.warning(f"Failed to connect to RPC {rpc_url}: {e}")
                continue
        
        logging.error("Failed to connect to any Mantle RPC")
        raise Exception("Failed to connect to any Mantle RPC")
    
    @classmethod
    def get_underlying_asset_address(cls, atoken_address: str) -> str:
        """
        Get underlying asset address from aToken address using optimized JSON lookup
        
        Args:
            atoken_address: Address of the aToken
            
        Returns:
            str: Address of the underlying asset
        """
        cache_key = f"underlying_{atoken_address.lower()}"
        cached_address = cache_manager.get_cached_data('lendle_isolated', 'mantle', cache_key)
        if cached_address:
            return cached_address.get('underlying_address')
        
        try:
            # Use optimized lookup
            reserve = cls._get_reserve_by_atoken(atoken_address)
            
            if not reserve:
                logging.warning(f"No reserve found for aToken {atoken_address} in JSON data")
                return None
            
            underlying_address = reserve.get('underlying_token_address')
            
            if underlying_address:
                # Cache underlying address for 1 day
                cache_manager.set_cached_data('lendle_isolated', 'mantle', cache_key, {
                    'underlying_address': underlying_address
                }, ttl=86400)
                
                logging.info(f"[DEBUG] Found underlying address {underlying_address[:8]}... for aToken {atoken_address[:8]}...")
                return underlying_address
            
            logging.warning(f"No underlying address found for aToken {atoken_address} in reserve data")
            return None
            
        except Exception as e:
            logging.error(f"Error getting underlying asset address from JSON for aToken {atoken_address}: {e}")
            return None
    
    @classmethod
    def find_provider_for_atoken(cls, atoken_address: str) -> str:
        """
        Find the addresses provider that contains the given aToken using optimized JSON lookup
        
        Args:
            atoken_address: Address of the aToken
            
        Returns:
            str: Address of the addresses provider that contains this aToken, or None if not found
        """
        cache_key = f"provider_for_atoken_{atoken_address.lower()}"
        cached_provider = cache_manager.get_cached_data('lendle_isolated', 'mantle', cache_key)
        if cached_provider:
            return cached_provider.get('provider_address')
        
        try:
            # Use optimized lookup
            reserve = cls._get_reserve_by_atoken(atoken_address)
            
            if not reserve:
                logging.warning(f"No reserve found for aToken {atoken_address} in JSON data")
                return None
            
            # Extract provider address from pool_data_provider (they are the same)
            provider_address = reserve.get('pool_data_provider')
            
            if provider_address:
                # Cache the result
                cache_manager.set_cached_data('lendle_isolated', 'mantle', cache_key, {
                    'provider_address': provider_address
                }, ttl=86400)
                
                logging.info(f"[DEBUG] Found provider {provider_address[:8]}... for aToken {atoken_address[:8]}...")
                return provider_address
            
            logging.warning(f"No provider address found for aToken {atoken_address}")
            return None
            
        except Exception as e:
            logging.error(f"Error finding provider for aToken {atoken_address}: {e}")
            return None
    
    @classmethod
    def get_all_addresses_providers(cls):
        """
        Get all addresses providers from the isolated markets JSON data
        
        Returns:
            List[str]: List of unique addresses provider addresses
        """
        try:
            # Load isolated markets data
            markets_data = cls._load_isolated_markets_data()
            reserves = markets_data.get('reserves', [])
            
            if not reserves:
                logging.warning("No reserves found in isolated markets data")
                return []
            
            # Extract unique pool_data_provider addresses (these are the providers)
            providers = set()
            for reserve in reserves:
                if isinstance(reserve, dict) and 'pool_data_provider' in reserve:
                    providers.add(Web3.to_checksum_address(reserve['pool_data_provider']))
            
            providers_list = list(providers)
            logging.info(f"Found {len(providers_list)} unique addresses providers from JSON data")
            return providers_list
            
        except Exception as e:
            logging.error(f"Error getting addresses providers from JSON: {e}")
            return []
    
    @classmethod
    def get_contract_addresses_for_provider(cls, addresses_provider_address: str):
        """
        Get contract addresses for a specific addresses provider from JSON data
        
        Args:
            addresses_provider_address: Address of the addresses provider (pool_data_provider)
            
        Returns:
            Dict: Contract addresses for this provider
        """
        cache_key = f"contract_addresses_{addresses_provider_address.lower()}"
        cached_addresses = cache_manager.get_cached_data('lendle_isolated', 'mantle', cache_key)
        if cached_addresses:
            return cached_addresses
        
        try:
            # Load isolated markets data
            markets_data = cls._load_isolated_markets_data()
            reserves = markets_data.get('reserves', [])
            
            if not reserves:
                logging.warning("No reserves found in isolated markets data")
                return {}
            
            # Find a reserve that uses this provider (pool_data_provider)
            for reserve in reserves:
                if not isinstance(reserve, dict):
                    continue
                    
                pool_data_provider = reserve.get('pool_data_provider')
                if pool_data_provider and pool_data_provider.lower() == addresses_provider_address.lower():
                    price_oracle = reserve.get('price_oracle')
                    addresses_provider = reserve.get('addresses_provider')
                    
                    if not price_oracle:
                        logging.error(f"No price oracle found for provider {pool_data_provider}")
                        return {}
                    
                    addresses = {
                        'addresses_provider': addresses_provider,
                        'pool_data_provider': pool_data_provider,
                        'price_oracle': price_oracle
                    }
                    
                    cache_manager.set_cached_data('lendle_isolated', 'mantle', cache_key, addresses, ttl=86400)
                    return addresses
            
            logging.warning(f"No reserves found for provider {addresses_provider_address}")
            return {}
            
        except Exception as e:
            logging.error(f"Error getting contract addresses for provider {addresses_provider_address}: {e}")
            return {}
    
    @classmethod
    def get_interest_rate_data(cls, token_address: str, provider_address: str = None) -> Dict:
        """
        Get interest rate data from the strategy contract
        """
        try:
            if not provider_address:
                logging.error("No provider address provided for interest rate data")
                return None
            
            # Get contract addresses for this provider
            contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
            if not contract_addresses or 'pool_data_provider' not in contract_addresses:
                logging.error(f"No pool data provider found for provider {provider_address}")
                return None
            
            web3 = cls.get_web3_provider()
            if not web3:
                logging.error("Failed to get web3 provider for interest rate data")
                return None
                
            pool_data_provider = web3.eth.contract(
                address=contract_addresses['pool_data_provider'],
                abi=POOL_DATA_PROVIDER_ABI
            )
            
            # Get the interest rate strategy address for this specific token
            strategy_address = pool_data_provider.functions.getInterestRateStrategyAddress(
                Web3.to_checksum_address(token_address)
            ).call()

            if not strategy_address or strategy_address == '0x0000000000000000000000000000000000000000':
                logging.warning(f"No interest rate strategy found for token {token_address}")
                return None
            
            # Create contract instance for the specific strategy
            strategy_contract = web3.eth.contract(
                address=Web3.to_checksum_address(strategy_address),
                abi=DEFAULT_RESERVE_INTEREST_RATE_STRATEGY_ABI
            )
            
            # Get interest rate data
            rate_data = strategy_contract.functions.getInterestRateData(
                Web3.to_checksum_address(token_address)
            ).call()
            
            if not rate_data or len(rate_data) < 4:
                logging.error(f"Invalid rate data format for token {token_address}: {rate_data}")
                return None
            
            result = {
                'optimal_usage_ratio': rate_data[0] / cls.RAY,
                'base_variable_borrow_rate': rate_data[1] / cls.RAY,
                'variable_rate_slope1': rate_data[2] / cls.RAY,
                'variable_rate_slope2': rate_data[3] / cls.RAY
            }
            
            return result
        except Exception as e:
            logging.error(f"Error getting interest rate data: {e}")
            return None
    
    @classmethod
    def get_token_price(cls, token_address: str, provider_address: str = None) -> float:
        """
        Get token price from the price oracle
        """
        try:
            if not provider_address:
                logging.error("No provider address provided for token price")
                return 0.0
            
            contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
            if not contract_addresses or 'price_oracle' not in contract_addresses:
                logging.error(f"No price oracle found for provider {provider_address}")
                return 0.0
            
            web3 = cls.get_web3_provider()
            if not web3:
                logging.error("Failed to get web3 provider for token price")
                return 0.0
                
            price_oracle = web3.eth.contract(
                address=contract_addresses['price_oracle'],
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
        if not isinstance(utilization, (int, float)) or utilization < 0:
            logging.warning(f"Invalid utilization: {utilization}")
            return 0.0
            
        if not rate_data or not isinstance(rate_data, dict):
            logging.warning("Invalid rate_data provided to calculate_borrow_rate")
            return 0.0
            
        required_keys = ['optimal_usage_ratio', 'base_variable_borrow_rate', 'variable_rate_slope1', 'variable_rate_slope2']
        if not all(key in rate_data for key in required_keys):
            logging.warning(f"Missing required keys in rate_data: {required_keys}")
            return 0.0
            
        try:
            if utilization <= rate_data['optimal_usage_ratio']:
                return rate_data['base_variable_borrow_rate'] + (rate_data['variable_rate_slope1'] * utilization)
            else:
                excess = utilization - rate_data['optimal_usage_ratio']
                return (rate_data['base_variable_borrow_rate'] + 
                       (rate_data['variable_rate_slope1'] * rate_data['optimal_usage_ratio']) +
                       (rate_data['variable_rate_slope2'] * excess))
        except Exception as e:
            logging.error(f"Error calculating borrow rate: {e}")
            return 0.0
    
    @classmethod
    def get_token_decimals(cls, token_address: str, provider_address: str = None) -> int:
        """
        Get token decimals from the reserve configuration
        
        Args:
            token_address: Address of the underlying token
            provider_address: Address of the provider
            
        Returns:
            int: Number of decimals for the token
        """
        if not provider_address:
            logging.warning("No provider_address provided to get_token_decimals, using default 18")
            return 18  # Default to 18 decimals
        
        contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
        if not contract_addresses:
            logging.warning(f"No contract addresses found for provider {provider_address} in get_token_decimals, using default 18")
            return 18
        
        try:
            web3 = cls.get_web3_provider()
            if not web3:
                logging.error("Failed to get web3 provider for token decimals")
                return 18
                
            pool_data_provider = web3.eth.contract(
                address=contract_addresses['pool_data_provider'],
                abi=POOL_DATA_PROVIDER_ABI
            )
            
            # Get reserve configuration data which includes decimals
            reserve_config = pool_data_provider.functions.getReserveConfigurationData(
                Web3.to_checksum_address(token_address)
            ).call()
            
            if not reserve_config or len(reserve_config) == 0:
                logging.error(f"Invalid reserve configuration for token {token_address}")
                return 18
            
            # Decimals is the first element in the configuration data
            decimals = reserve_config[0]
            if not decimals:
                logging.error(f"Invalid decimals for token {token_address}: {decimals}")
                return 18
                
            return int(decimals)
        except Exception as e:
            logging.error(f"Error getting token decimals for {token_address}: {e}")
            return 18  # Default to 18 decimals
    
    @classmethod
    def get_total_supplied(cls, token_address: str, provider_address: str = None) -> float:
        if not provider_address:
            logging.error("No provider address provided for total supplied")
            return 0.0
        
        if not token_address:
            logging.error("No token address provided for total supplied")
            return 0.0
        
        contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
        if not contract_addresses or 'pool_data_provider' not in contract_addresses:
            logging.error(f"No pool data provider found for provider {provider_address}")
            return 0.0
        
        web3 = cls.get_web3_provider()
        if not web3:
            logging.error("Failed to get web3 provider for total supplied")
            return 0.0
            
        try:
            pool_data_provider = web3.eth.contract(
                address=contract_addresses['pool_data_provider'],
                abi=POOL_DATA_PROVIDER_ABI
            )
            total_supply = pool_data_provider.functions.getATokenTotalSupply(
                Web3.to_checksum_address(token_address)
            ).call()
            
            if not total_supply or total_supply < 0:
                logging.error(f"Invalid total supply for token {token_address}: {total_supply}")
                return 0.0
            
            # Get token decimals and scale accordingly
            decimals = cls.get_token_decimals(token_address, provider_address)
            scaled_supply = float(total_supply) / (10 ** decimals)
            return scaled_supply
        except Exception as e:
            logging.error(f"Error getting total supplied for token {token_address}: {e}")
            return 0.0

    @classmethod
    def get_total_borrowed(cls, token_address: str, provider_address: str = None) -> float:
        if not provider_address:
            logging.error("No provider address provided for total borrowed")
            return 0.0
        
        contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
        if not contract_addresses or 'pool_data_provider' not in contract_addresses:
            logging.error(f"No pool data provider found for provider {provider_address}")
            return 0.0
        
        web3 = cls.get_web3_provider()
        if not web3:
            logging.error("Failed to get web3 provider for total borrowed")
            return 0.0
            
        pool_data_provider = web3.eth.contract(
            address=contract_addresses['pool_data_provider'],
            abi=POOL_DATA_PROVIDER_ABI
        )
        total_borrowed = pool_data_provider.functions.getTotalDebt(
            Web3.to_checksum_address(token_address)
        ).call()
        
        if not total_borrowed:
            logging.error(f"Invalid total borrowed for token {token_address}: {total_borrowed}")
            return 0.0
        
        # Get token decimals and scale accordingly
        decimals = cls.get_token_decimals(token_address, provider_address)
        scaled_borrowed = float(total_borrowed) / (10 ** decimals)
        return scaled_borrowed
    
    @classmethod
    def get_all_reserves_from_all_providers(cls):
        """
        Get all reserves from isolated markets JSON data with full aToken mapping
        
        Returns:
            List[Dict]: List of reserve data from JSON with aToken addresses
        """
        cache_key = "all_reserves_mapping"
        cached_mapping = cache_manager.get_cached_data('lendle_isolated', 'mantle', cache_key)
        if cached_mapping:
            return cached_mapping.get('reserves', [])
        
        try:
            # Load isolated markets data (this will also create the lookup table)
            markets_data = cls._load_isolated_markets_data()
            reserves = markets_data.get('reserves', [])
            
            if not reserves:
                logging.warning("No reserves found in isolated markets data")
                return []
            
            all_reserves = []
            
            for reserve in reserves:
                if not isinstance(reserve, dict):
                    logging.warning(f"Invalid reserve format: {reserve}")
                    continue
                
                # Extract data from JSON
                atoken_address = reserve.get('id')
                underlying_token_address = reserve.get('underlying_token_address')
                pool_data_provider = reserve.get('pool_data_provider')
                price_oracle = reserve.get('price_oracle')
                
                if not all([atoken_address, underlying_token_address, pool_data_provider, price_oracle]):
                    logging.warning(f"Incomplete reserve data: {reserve}")
                    continue
                
                # Get token symbol from utils
                try:
                    symbol = get_reserve_name(underlying_token_address) or f'Token-{underlying_token_address[:8]}'
                except Exception:
                    symbol = f'Token-{underlying_token_address[:8]}'
                
                all_reserves.append({
                    'symbol': symbol,
                    'token_address': underlying_token_address,
                    'atoken_address': atoken_address,
                    'provider_address': pool_data_provider,  # pool_data_provider is the provider
                    'pool_data_provider': pool_data_provider,
                    'price_oracle': price_oracle
                })
            
            # Cache the full mapping for 1 hour
            cache_manager.set_cached_data('lendle_isolated', 'mantle', cache_key, {
                'reserves': all_reserves
            }, ttl=3600)
            
            logging.info(f"[DEBUG] Total reserves found from JSON: {len(all_reserves)}")
            return all_reserves
            
        except Exception as e:
            logging.error(f"Error getting all reserves from JSON: {e}")
            return []
    
    @classmethod
    def get_reserves_incentives_data(cls, provider_address: str = None) -> Dict[str, Dict]:
        """
        Get incentive data for reserves from a specific provider
        
        Args:
            provider_address: Address of the provider (pool_data_provider address)
            
        Returns:
            Dict[str, Dict]: Dictionary with token addresses as keys and incentive data as values
        """
        try:
            if not provider_address:
                # Use first available provider for backward compatibility
                addresses_providers = cls.get_all_addresses_providers()
                if not addresses_providers:
                    return {}
                provider_address = addresses_providers[0]
            
            cache_key = f"incentives_{provider_address.lower()}"
            cached_incentives = cache_manager.get_cached_data('lendle_isolated_incentives', 'mantle', cache_key)
            if cached_incentives:
                return cached_incentives.get('incentives', {})
            
            contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
            if not contract_addresses:
                logging.warning(f"No contract addresses found for provider {provider_address}")
                return {}
            
            # Get the addresses_provider from contract_addresses (this is what we need for incentives)
            addresses_provider = contract_addresses.get('addresses_provider')
            if not addresses_provider:
                logging.error(f"No addresses_provider found in contract_addresses for provider {provider_address}")
                return {}
            
            web3 = cls.get_web3_provider()
            if not web3:
                logging.error(f"Failed to get web3 provider for incentives data")
                return {}
                
            incentive_provider = web3.eth.contract(
                address=Web3.to_checksum_address(cls.INCENTIVE_DATA_PROVIDER_ADDRESS),
                abi=INCENTIVE_DATA_PROVIDER_ABI
            )
            
            # Use the addresses_provider (not the pool_data_provider) for incentives data
            incentives_data = incentive_provider.functions.getReservesIncentivesData(
                addresses_provider
            ).call()
            
            if not incentives_data:
                logging.warning(f"No incentives data found for provider {provider_address}")
                return {}
            
            result = {}
            for item in incentives_data:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    logging.warning(f"Invalid incentives item format: {item}")
                    continue
                    
                underlying_asset = item[0].lower()
                a_incentive_data = item[1]
                v_incentive_data = item[2]
                
                if not isinstance(a_incentive_data, (list, tuple)) or len(a_incentive_data) < 3:
                    logging.warning(f"Invalid aIncentiveData format: {a_incentive_data}")
                    a_incentive_data = [None, None, []]
                    
                if not isinstance(v_incentive_data, (list, tuple)) or len(v_incentive_data) < 3:
                    logging.warning(f"Invalid vIncentiveData format: {v_incentive_data}")
                    v_incentive_data = [None, None, []]
                
                a_rewards_info = []
                if a_incentive_data[2] and isinstance(a_incentive_data[2], (list, tuple)) and len(a_incentive_data[2]) > 0:
                    for reward_info in a_incentive_data[2]:
                        if not isinstance(reward_info, (list, tuple)) or len(reward_info) < 11:
                            logging.warning(f"Invalid aToken reward info format: {reward_info}")
                            continue
                            
                        try:
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
                        except (ValueError, TypeError) as e:
                            logging.warning(f"Error processing aToken reward info: {e}")
                            continue
                
                v_rewards_info = []
                if v_incentive_data[2] and isinstance(v_incentive_data[2], (list, tuple)) and len(v_incentive_data[2]) > 0:
                    for reward_info in v_incentive_data[2]:
                        if not isinstance(reward_info, (list, tuple)) or len(reward_info) < 11:
                            logging.warning(f"Invalid vToken reward info format: {reward_info}")
                            continue
                            
                        try:
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
                        except (ValueError, TypeError) as e:
                            logging.warning(f"Error processing vToken reward info: {e}")
                            continue
                
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
            
            # Validate input parameters
            if not isinstance(incentive_data, dict):
                logging.warning("Invalid incentive_data format, using empty dict")
                incentive_data = {}
                
            if not isinstance(total_supplied, (int, float)):
                logging.warning(f"Invalid total_supplied: {total_supplied}")
                total_supplied = 0.0
                
            if not isinstance(total_borrowed, (int, float)):
                logging.warning(f"Invalid total_borrowed: {total_borrowed}")
                total_borrowed = 0.0
                
            if not isinstance(token_price, (int, float)):
                logging.warning(f"Invalid token_price: {token_price}")
                token_price = 1.0
            
            # Calculate supply APR from aToken incentives
            a_incentive_data = incentive_data.get('aIncentiveData', {})
            if isinstance(a_incentive_data, dict) and a_incentive_data.get('rewards'):
                rewards = a_incentive_data['rewards']
                if isinstance(rewards, list):
                    for reward in rewards:
                        if not isinstance(reward, dict):
                            logging.warning(f"Invalid reward format: {reward}")
                            continue
                            
                        # Skip if emission is 0 or ended
                        emission_per_second = reward.get('emissionPerSecond', 0)
                        emission_end_timestamp = reward.get('emissionEndTimestamp', 0)
                        incentives_last_update_timestamp = reward.get('incentivesLastUpdateTimestamp', 0)
                        
                        if emission_per_second == 0 or (emission_end_timestamp > 0 and 
                           emission_end_timestamp < incentives_last_update_timestamp):
                            continue
                        
                        try:
                            # Convert emissionPerSecond to right decimals
                            reward_token_decimals = reward.get('rewardTokenDecimals', 18)
                            emission_per_second_scaled = emission_per_second / (10 ** reward_token_decimals)
                            
                            # Convert reward price feed to right decimals
                            reward_price_feed = reward.get('rewardPriceFeed', 0)
                            price_feed_decimals = reward.get('priceFeedDecimals', 8)
                            reward_price_feed_scaled = reward_price_feed / (10 ** price_feed_decimals)
                            
                            # Get hardcoded reward token price
                            reward_token_symbol = reward.get('rewardTokenSymbol', '')
                            reward_token_price = cls.get_reward_token_price(reward_token_symbol)
                            
                            # Calculate yearly rewards in USD
                            yearly_rewards_usd = emission_per_second_scaled * cls.SECONDS_IN_YEAR * reward_price_feed_scaled * reward_token_price
                            
                            # Calculate total supply in USD
                            total_supplied_usd = total_supplied * token_price
                            
                            if total_supplied_usd > 0:
                                # Calculate annual rewards rate (APR not APY)
                                # This is a simple linear rate, not compounded
                                supply_apr += yearly_rewards_usd / total_supplied_usd
                        except (ValueError, TypeError, ZeroDivisionError) as e:
                            logging.warning(f"Error calculating supply APR for reward {reward}: {e}")
                            continue
            
            # Calculate borrow APR from vToken incentives
            v_incentive_data = incentive_data.get('vIncentiveData', {})
            if isinstance(v_incentive_data, dict) and v_incentive_data.get('rewards'):
                rewards = v_incentive_data['rewards']
                if isinstance(rewards, list):
                    for reward in rewards:
                        if not isinstance(reward, dict):
                            logging.warning(f"Invalid reward format: {reward}")
                            continue
                            
                        # Skip if emission is 0 or ended
                        emission_per_second = reward.get('emissionPerSecond', 0)
                        emission_end_timestamp = reward.get('emissionEndTimestamp', 0)
                        incentives_last_update_timestamp = reward.get('incentivesLastUpdateTimestamp', 0)
                        
                        if emission_per_second == 0 or (emission_end_timestamp > 0 and 
                           emission_end_timestamp < incentives_last_update_timestamp):
                            continue
                        
                        try:
                            # Convert emissionPerSecond to right decimals
                            reward_token_decimals = reward.get('rewardTokenDecimals', 18)
                            emission_per_second_scaled = emission_per_second / (10 ** reward_token_decimals)
                            
                            # Convert reward price feed to right decimals
                            reward_price_feed = reward.get('rewardPriceFeed', 0)
                            price_feed_decimals = reward.get('priceFeedDecimals', 8)
                            reward_price_feed_scaled = reward_price_feed / (10 ** price_feed_decimals)
                            
                            # Get hardcoded reward token price
                            reward_token_symbol = reward.get('rewardTokenSymbol', '')
                            reward_token_price = cls.get_reward_token_price(reward_token_symbol)
                            
                            # Calculate yearly rewards in USD
                            yearly_rewards_usd = emission_per_second_scaled * cls.SECONDS_IN_YEAR * reward_price_feed_scaled * reward_token_price
                            
                            # Calculate total borrowed in USD
                            total_borrowed_usd = total_borrowed * token_price
                            
                            if total_borrowed_usd > 0:
                                # Calculate annual rewards rate (APR not APY)
                                # This is a simple linear rate, not compounded
                                borrow_apr += yearly_rewards_usd / total_borrowed_usd
                        except (ValueError, TypeError, ZeroDivisionError) as e:
                            logging.warning(f"Error calculating borrow APR for reward {reward}: {e}")
                            continue
            
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
            if not investment or not isinstance(investment, dict):
                logging.warning("Invalid investment data provided to calculate_reserve_apy")
                return 0, 0, 0, 0, 0, 0
                
            if not isinstance(our_supply, (int, float)) or our_supply < 0:
                logging.warning(f"Invalid our_supply: {our_supply}")
                our_supply = 0.0
            
            # Get total borrowed and supplied from investment data, already scaled correctly
            total_borrowed = investment.get('total_borrowed', 0)
            total_supplied = investment.get('total_supplied', 0)
            
            if not isinstance(total_borrowed, (int, float)) or total_borrowed < 0:
                logging.warning(f"Invalid total_borrowed: {total_borrowed}")
                total_borrowed = 0.0
                
            if not isinstance(total_supplied, (int, float)) or total_supplied < 0:
                logging.warning(f"Invalid total_supplied: {total_supplied}")
                total_supplied = 0.0
            
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
                        if reward.get('emissionPerSecond', 0) == 0 or (reward.get('emissionEndTimestamp', 0) > 0 and 
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
                            # Convert from percentage to decimal
                            rewards_apy += yearly_rewards_usd / total_supplied_usd
            else:
                # Fallback to pre-calculated rewards if incentives data not available
                # Convert from percentage to decimal if it's stored as percentage
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
        Fetch and process Lendle reserve data
        This version expects aToken address and finds the correct provider
        """
        try:
            # Get aToken address from fund_data
            atoken_address = fund_data['address']
            atoken_address = Web3.to_checksum_address(atoken_address)
            
            logging.info(f"[DEBUG] Processing aToken: {atoken_address}")
            
            # Find the provider for this aToken
            try:
                provider_address = cls.find_provider_for_atoken(atoken_address)
                if not provider_address:
                    logging.error(f"Could not find provider for aToken {atoken_address}")
                    return None
                
                logging.info(f"[DEBUG] Found provider: {provider_address[:8]}...")
            except Exception as e:
                logging.error(f"Error finding provider for aToken {atoken_address}: {e}")
                return None
            
            # Get contract addresses for this provider
            try:
                contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
                if not contract_addresses:
                    logging.error(f"Could not get contract addresses for provider {provider_address}")
                    return None
            except Exception as e:
                logging.error(f"Error getting contract addresses for provider {provider_address}: {e}")
                return None
            
            # Get the underlying token address from the aToken
            try:
                token_address = cls.get_underlying_asset_address(atoken_address)
                if not token_address:
                    logging.error(f"Could not get underlying asset address from aToken {atoken_address}")
                    return None
                    
                token_address = Web3.to_checksum_address(token_address)
                logging.info(f"[DEBUG] Underlying token: {token_address[:8]}...")
            except Exception as e:
                logging.error(f"Error getting underlying asset address for aToken {atoken_address}: {e}")
                return None

            # Try to get cached static data (1 day TTL) - Use atoken address for reserve-specific caching
            static_cache_key = f"static_{atoken_address}"
            cached_static = cache_manager.get_cached_data('lendle_isolated', 'mantle', static_cache_key)
            
            # Try to get cached dynamic data (10 min TTL) - Use atoken address for reserve-specific caching
            dynamic_cache_key = f"dynamic_{atoken_address}"
            cached_dynamic = cache_manager.get_cached_data('lendle_isolated', 'mantle', dynamic_cache_key)
            
            # Get dynamic data (total_borrowed, total_supplied)
            if cached_dynamic:
                total_borrowed = cached_dynamic['total_borrowed']
                total_supplied = cached_dynamic['total_supplied']
            else:
                # Batch dynamic data calls
                try:
                    total_borrowed = cls.get_total_borrowed(token_address, provider_address)
                    total_supplied = cls.get_total_supplied(token_address, provider_address)
                    
                    # Validate the returned values
                    if total_borrowed is None or total_supplied is None:
                        logging.error(f"Failed to get dynamic data for token {token_address}")
                        return None
                        
                except Exception as e:
                    logging.error(f"Error getting dynamic data for token {token_address}: {e}")
                    return None
                
                # Cache dynamic data for 10 minutes
                cache_manager.set_cached_data('lendle_isolated', 'mantle', dynamic_cache_key, {
                    'total_borrowed': total_borrowed,
                    'total_supplied': total_supplied
                }, ttl=600)
            
            # Calculate utilization rate from cached/fresh data
            utilization = total_borrowed / total_supplied if total_supplied > 0 else 0
            
            # Get incentives data for this specific provider
            incentives_cache_key = f"incentives_{provider_address.lower()}"
            cached_incentives = cache_manager.get_cached_data('lendle_isolated_incentives', 'mantle', incentives_cache_key)
            
            if cached_incentives:
                incentives_data = cached_incentives.get('incentives', {})
            else:
                try:
                    incentives_data = cls.get_reserves_incentives_data(provider_address)
                    if not incentives_data:
                        logging.warning(f"No incentives data found for provider {provider_address}")
                        incentives_data = {}
                except Exception as e:
                    logging.error(f"Error getting incentives data for provider {provider_address}: {e}")
                    incentives_data = {}
            
            token_lower = token_address.lower()

            incentive_data = incentives_data.get(token_lower, {})
            
            # Ensure incentive_data is a dictionary
            if not isinstance(incentive_data, dict):
                logging.warning(f"Invalid incentive_data format for token {token_address}, using empty dict")
                incentive_data = {}
            
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
                rate_data = cls.get_interest_rate_data(token_address, provider_address)
                if not rate_data:
                    logging.error(f"Failed to get interest rate data for token {token_address}")
                    return None
                
                token_price = cls.get_token_price(token_address, provider_address)
                
                contract_addresses = cls.get_contract_addresses_for_provider(provider_address)
                if contract_addresses and 'pool_data_provider' in contract_addresses:
                    try:
                        web3 = cls.get_web3_provider()
                        pool_data_provider = web3.eth.contract(
                            address=contract_addresses['pool_data_provider'],
                            abi=POOL_DATA_PROVIDER_ABI
                        )
                        reserve_config = pool_data_provider.functions.getReserveConfigurationData(token_address).call()
                        reserve_factor = reserve_config[4] / 1e4 if len(reserve_config) > 4 else 0.1
                    except Exception as e:
                        logging.error(f"Error getting reserve configuration for token {token_address}: {e}")
                        reserve_factor = 0.1
                else:
                    logging.warning(f"No pool_data_provider found for provider {provider_address}, using default reserve_factor")
                    reserve_factor = 0.1
                
                try:
                    base_reserve_name = get_reserve_name(token_address)
                    if not base_reserve_name:
                        base_reserve_name = f'Reserve-{token_address[:8]}'
                    reserve_name = f"{base_reserve_name} Reserve"
                except Exception as e:
                    logging.error(f"Error getting reserve name for token {token_address}: {e}")
                    reserve_name = f"Reserve-{token_address[:8]} Reserve"
                
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

            logging.info(f"[DEBUG] Incentive APRs: {incentive_aprs}")
            
            # Validate incentive_aprs
            if not incentive_aprs or not all(key in incentive_aprs for key in ['supply_apr', 'borrow_apr']):
                logging.error(f"Invalid incentive_aprs for token {token_address}: {incentive_aprs}")
                return None
            
            # Validate rate_data before using it
            if not rate_data or not all(key in rate_data for key in ['optimal_usage_ratio', 'variable_rate_slope1', 'variable_rate_slope2', 'base_variable_borrow_rate']):
                logging.error(f"Invalid rate_data for token {token_address}: {rate_data}")
                return None
            
            # Create the reserve info dictionary
            reserve_info = {
                'id': atoken_address,
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

            logging.info(f"[DEBUG] Reserve info: {reserve_info}")

            return reserve_info
        except Exception as e:
            logging.error(f"Error processing Lendle reserve {fund_data.get('address')}: {str(e)}")
            return None

    @classmethod
    def get_reward_token_price(cls, reward_token_symbol: str) -> float:
        """
        Get hardcoded price for reward tokens
        """
        if not reward_token_symbol:
            logging.warning("Empty reward token symbol provided")
            return 0.0
            
        price = cls.REWARD_TOKEN_PRICES.get(reward_token_symbol, 0.0)
            
        return price
    
    @classmethod
    def get_reserve_id(cls, reserve_data: Dict) -> str:
        """
        Get the reserve ID (aToken address) for API responses
        
        Args:
            reserve_data: Reserve data dictionary
            
        Returns:
            str: aToken address to be used as ID in API responses
        """
        if not reserve_data or not isinstance(reserve_data, dict):
            logging.warning("Invalid reserve_data provided to get_reserve_id")
            return None
            
        reserve_id = reserve_data.get('id')
        if not reserve_id:
            logging.warning("No ID found in reserve_data")
            
        return reserve_id
    
    @classmethod
    def format_for_api_response(cls, reserve_data: Dict) -> Dict:
        """
        Format reserve data for API response ensuring aToken address is used as ID
        
        Args:
            reserve_data: Reserve data dictionary
            
        Returns:
            Dict: Formatted data with aToken address as main identifier
        """
        if not reserve_data:
            logging.warning("No reserve_data provided to format_for_api_response")
            return None
            
        if not isinstance(reserve_data, dict):
            logging.warning(f"Invalid reserve_data type: {type(reserve_data)}")
            return None
            
        try:
            return reserve_data.copy()
        except Exception as e:
            logging.error(f"Error copying reserve_data: {e}")
            return None 
