"""
Morpho Protocol Adapter for Base network
"""
import json
import logging
import requests
import math
import asyncio
import aiohttp
from functools import lru_cache
from datetime import datetime, timedelta
from web3 import Web3, constants
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_token_price

# Set up logger
logger = logging.getLogger(__name__)

class TTLCache:
    """Simple TTL cache implementation"""
    def __init__(self, default_ttl=300):  # 5 minutes default
        self.cache = {}
        self.default_ttl = default_ttl
    
    def get(self, key):
        if key in self.cache:
            value, expiry = self.cache[key]
            if datetime.now() < expiry:
                return value
            else:
                del self.cache[key]
        return None
    
    def set(self, key, value, ttl=None):
        if ttl is None:
            ttl = self.default_ttl
        expiry = datetime.now() + timedelta(seconds=ttl)
        self.cache[key] = (value, expiry)
    
    def clear(self):
        self.cache.clear()

class MorphoBaseAdapter(BaseProtocolAdapter):
    """
    Adapter for Morpho protocol on Base network
    """
    
    SOURCE = "Morpho Base"
    PROTOCOL_NAME = "Morpho"
    NETWORK_NAME = "Base"
    RPC_URL = "https://mainnet.base.org"
    GRAPHQL_API_URL = "https://blue-api.morpho.org/graphql"
    MORPHO_API_URL = "https://api.morpho.org/graphql"
    # IRM contract ABI for rateAtTarget calculation
    IRM_ABI = [
        {"inputs":[{"internalType":"Id","name":"","type":"bytes32"}],"name":"rateAtTarget","outputs":[{"internalType":"int256","name":"","type":"int256"}],"stateMutability":"view","type":"function"}
    ]
    # Seconds in a year for APY calculation
    SECONDS_IN_YEAR = 31536000
    TARGET_UTILIZATION = 0.9  # 90% target utilization
    CURVE_STEEPNESS = 4  # Fixed parameter that determines the steepness of the curve
    
    # Cache instance for market data
    _market_cache = TTLCache(default_ttl=3600)  # 1 hour cache
    
    # GraphQL query for fetching markets
    MARKETS_QUERY = """
    query GetMarkets($first: Int, $where: MarketFilters, $orderBy: MarketOrderBy) {
        markets(first: $first, where: $where, orderBy: $orderBy) {
            items {
                loanAsset {
                    address
                    decimals
                    symbol
                    priceUsd
                    chain {
                        network
                    }
                }
                uniqueKey
                state {
                    price
                    fee
                    supplyAssets
                    borrowAssets
                    supplyShares
                    borrowShares
                    timestamp
                    rewards {
                        yearlySupplyTokens
                        asset {
                            decimals
                            priceUsd
                        }
                    }
                }
                collateralAsset {
                    address
                    decimals
                    symbol
                    priceUsd
                    chain {
                        network
                    }
                }
                irmAddress
                lltv
                oracleAddress
            }
        }
    }
    """
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from Morpho protocol
        """
        # Check in source field
        if fund_data.get('source') == cls.SOURCE:
            return True
        return False
    
    @classmethod
    def get_r_90(cls, current_rate_per_second, current_utilization):
        """
        Extract r_90 (rate at target utilization) from the current rate_per_second and utilization.
        """
        u = current_utilization
        u_target = cls.TARGET_UTILIZATION
        k = cls.CURVE_STEEPNESS
        if u > u_target:
            error = (u - u_target) / (1 - u_target)
            curve = (k - 1) * error + 1
        else:
            error = (u - u_target) / u_target
            curve = (1 - 1 / k) * error + 1
        r_90 = float(current_rate_per_second) / curve

        return r_90

    @classmethod
    def adaptive_curve_borrow_rate(cls, utilization, r_90):
        """
        Calculate the borrow rate per second for any utilization using AdaptiveCurveIRM.
        """
        u = utilization
        u_target = cls.TARGET_UTILIZATION
        k = cls.CURVE_STEEPNESS
        if u > u_target:
            error = (u - u_target) / (1 - u_target)
            curve = (k - 1) * error + 1
        else:
            error = (u - u_target) / u_target
            curve = (1 - 1 / k) * error + 1
        rate = r_90 * curve

        return rate
    
    @classmethod
    async def fetch_morpho_markets_data(cls, first=100, loan_asset_address=None, order_by=None, unique_keys=None):
        """
        Fetch market data from Morpho's GraphQL API
        
        Args:
            first: Number of markets to fetch
            loan_asset_address: Filter by loan asset address (optional)
            order_by: Order by field (optional)
            unique_keys: List of unique keys to filter by (optional)
            
        Returns:
            List of market data dictionaries
        """
        try:
            where = {"whitelisted": True}
            if loan_asset_address:
                where["loanAssetAddress_in"] = loan_asset_address
            if unique_keys:
                where["uniqueKey_in"] = unique_keys
            variables = {
                "first": first,
                "where": where
            }
            if order_by:
                variables["orderBy"] = order_by
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    cls.MORPHO_API_URL,
                    json={"query": cls.MARKETS_QUERY, "variables": variables}
                ) as response:
                    if response.status != 200:
                                            logger.error(f"Failed to fetch Morpho markets: {response.status}")
                    return []
                
                data = await response.json()
                if "errors" in data:
                    logger.error(f"GraphQL errors: {data['errors']}")
                    return []
                    
                    return data["data"]["markets"]["items"]
        except Exception as e:
            logger.error(f"Error fetching Morpho markets: {str(e)}")
            return []
    
    @classmethod
    def transform_market_data(cls, market):
        """
        Transform raw market data into calculator-compatible format
        
        Args:
            market: Raw market data from GraphQL
            
        Returns:
            Transformed market data
        """
        try:
            loan_asset = market["loanAsset"]
            collateral_asset = market["collateralAsset"]
            state = market["state"]
            unique_key = market.get("uniqueKey")
            
            # Calculate utilization
            total_supplied = float(state["supplyAssets"]) / (10 ** int(loan_asset["decimals"]))
            total_borrowed = float(state["borrowAssets"]) / (10 ** int(loan_asset["decimals"]))
            utilization = total_borrowed / total_supplied if total_supplied > 0 else 0
            
            # Get rewards info - TEMPORARILY DISABLED
            # Set all rewards to 0 to exclude them from calculations
            rewards = state.get("rewards", [])
            yearly_supply_tokens = "0"
            reward_token_decimals = 18
            reward_token_price = "0"
            
            # Original rewards logic (commented out for now):
            # if rewards:
            #     reward = rewards[0]
            #     yearly_supply_tokens = reward["yearlySupplyTokens"]
            #     reward_token_decimals = int(reward["asset"]["decimals"])
            #     reward_token_price = str(reward["asset"]["priceUsd"])
            
            # Build name
            if collateral_asset and collateral_asset.get("symbol") and loan_asset.get("symbol"):
                if collateral_asset["symbol"] == loan_asset["symbol"]:
                    name = f"{loan_asset['symbol']} Market"
                else:
                    name = f"{collateral_asset['symbol']}/{loan_asset['symbol']} Market"
            else:
                name = f"{loan_asset.get('symbol', 'Unknown')} Market"
            
            return {
                "id": unique_key,
                "name": name,
                "network": loan_asset["chain"]["network"].lower(),
                "protocol": "Morpho",
                "source": "Morpho Base",
                "token_price": float(loan_asset["priceUsd"]),
                "total_supplied": total_supplied,
                "total_borrowed": total_borrowed,
                "fee_percentage": float(state["fee"]) / 1e18,
                "optimal_usage_ratio": 0.9,  # Updated to match expected format
                "reserve_factor": 0,
                "yearlySupplyTokens": yearly_supply_tokens,
                "rewardTokenDecimals": reward_token_decimals,
                "rewardTokenPriceUsd": reward_token_price,
                "rate_per_second": "0"  # Will be updated with contract call if needed
            }
        except Exception as e:
            logger.error(f"Error transforming market data: {str(e)}")
            return None
    


    @classmethod
    async def get_market_by_unique_key_optimized(cls, unique_key):
        """
        Optimized method to get a specific market by its unique key using targeted GraphQL query with caching
        
        Args:
            unique_key: Market unique key (address in the request)
            
        Returns:
            Transformed market data or None if not found
        """
        try:
            # Check cache first
            cached_result = cls._market_cache.get(unique_key)
            if cached_result:
                logger.info(f"Cache hit for market {unique_key}")
                return cached_result
            
            logger.info(f"Cache miss for market {unique_key}, fetching with targeted GraphQL query")
            
            # Use original function with targeted unique_keys filter
            from protocols.morpho_markets import get_morpho_markets_data
            
            markets = await get_morpho_markets_data(first=1, unique_keys=[unique_key])
            
            if not markets:
                logger.warning(f"Market with unique key {unique_key} not found")
                return None
            
            # Get the market data (already transformed with contract calls)
            market_data = markets[0]
            
            # Update source to Morpho Base for this adapter
            market_data["source"] = "Morpho Base"
            
            # Cache the result
            cls._market_cache.set(unique_key, market_data)
            
            logger.info(f"Successfully fetched and cached market {unique_key}")
            return market_data
                    
        except Exception as e:
            logger.error(f"Error getting market by unique key (optimized): {str(e)}")
            return None

    @classmethod
    async def get_market_by_unique_key(cls, unique_key):
        """
        Get a specific market by its unique key - now uses optimized version
        
        Args:
            unique_key: Market unique key (address in the request)
            
        Returns:
            Transformed market data or None if not found
        """
        return await cls.get_market_by_unique_key_optimized(unique_key)
    
    @classmethod
    def clear_market_cache(cls):
        """
        Clear the market data cache
        """
        cls._market_cache.clear()
        logger.info("Market cache cleared")
    
    @classmethod
    async def get_markets_batch(cls, unique_keys):
        """
        Optimized batch method to get multiple markets with caching using targeted GraphQL query
        
        Args:
            unique_keys: List of market unique keys
            
        Returns:
            Dictionary mapping unique_key -> market_data
        """
        try:
            results = {}
            cache_misses = []
            
            # Check cache for each key
            for unique_key in unique_keys:
                cached_result = cls._market_cache.get(unique_key)
                if cached_result:
                    logger.info(f"Cache hit for market {unique_key}")
                    results[unique_key] = cached_result
                else:
                    cache_misses.append(unique_key)
            
            # If we have cache misses, fetch them all at once with targeted query
            if cache_misses:
                logger.info(f"Cache miss for {len(cache_misses)} markets, fetching with targeted GraphQL query")
                
                # Use original function with targeted unique_keys filter
                from protocols.morpho_markets import get_morpho_markets_data
                
                markets = await get_morpho_markets_data(first=len(cache_misses), unique_keys=cache_misses)
                
                # Cache missing markets (already transformed with contract calls)
                for market_data in markets:
                    market_id = market_data.get("id")
                    if market_id in cache_misses:
                        # Update source to Morpho Base
                        market_data["source"] = "Morpho Base"
                        
                        # Cache the result
                        cls._market_cache.set(market_id, market_data)
                        
                        # Add to results
                        results[market_id] = market_data
                        
                        logger.info(f"Successfully fetched and cached market {market_id}")
            
            return results
                    
        except Exception as e:
            logger.error(f"Error getting markets batch: {str(e)}")
            return {}
    
    @classmethod
    async def get_borrow_rate_from_contract(cls, market):
        """
        Get borrow rate from the interest rate model contract
        
        Args:
            market: Market data dictionary from GraphQL
            
        Returns:
            Borrow rate per second as a string, or None if failed
        """
        try:
            # This is a simplified version - for full functionality, 
            # we would need network config and contract ABI like in morpho_markets.py
            # For now, we return None to keep rate_per_second as "0"
            return None
        except Exception as e:
            logger.error(f"Error getting borrow rate from contract: {str(e)}")
            return None
    
    @classmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        """
        Calculate borrow rate for Morpho reserves
        
        This is a simplified implementation as Morpho uses adaptive curves and we need rateAtTarget from the contract
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
    def calculate_morpho_borrow_apy(cls, rate_per_second):
        """
        Calculate borrow APY using the exponential formula: exp(rate * seconds) - 1
        
        Args:
            rate_per_second: Interest rate per second (already in per-second units)
            
        Returns:
            float: The borrow APY as a percentage
        """
        try:
            rate = float(rate_per_second)  # Already in per-second units
            apy = math.exp(rate * cls.SECONDS_IN_YEAR) - 1
            if apy > 1:
                return 0
            return apy
        except Exception as e:
            logger.error(f"Error calculating Morpho borrow APY: {str(e)}")
            return 0
    
    @classmethod
    def calculate_morpho_supply_rates(cls, borrow_apy, utilization, fee):
        """
        Calculate supply APY based on borrow APY, utilization, and fee
        
        Args:
            borrow_apy: The borrow APY as a percentage
            utilization: The utilization rate as a decimal (e.g., 0.8 for 80%)
            fee: The fee rate in Wei (18 decimals)
            
        Returns:
            tuple: (supply_apr, supply_apy) as percentages
        """
        try:
            fee_decimal = float(fee) / 1e18
            supply_apy = borrow_apy * utilization * (1 - fee_decimal)
            supply_apr = 365 * ((1 + supply_apy) ** (1/365) - 1) if supply_apy > 0 else 0

            return supply_apr, supply_apy
        except Exception as e:
            logger.error(f"Error calculating Morpho supply rates: {str(e)}")
            return 0, 0
    
    @classmethod
    def query_graphql(cls, query):
        """
        Query the Morpho GraphQL API
        
        Args:
            query: GraphQL query string
            
        Returns:
            dict: Response data or None if failed
        """
        try:
            response = requests.post(
                cls.GRAPHQL_API_URL,
                json={"query": query},
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"GraphQL API error: {response.status_code}, {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error querying GraphQL API: {str(e)}")
            return None
    
    @classmethod
    def get_vault_data(cls, vault_address):
        """
        Get vault data from GraphQL API
        
        Args:
            vault_address: Vault contract address
            
        Returns:
            dict: Vault data or None if failed
        """
        query = """
        query {
          vaultByAddress(
            address: "%s"
            chainId: 8453
          ) {
            address
            state {
              totalAssets
              totalAssetsUsd
              totalSupply
              apy
              netApy
              netApyWithoutRewards
              dailyApy
              dailyNetApy
              weeklyApy
              weeklyNetApy
              monthlyApy
              monthlyNetApy
              rewards {
                asset {
                  address
                }
                supplyApr
                yearlySupplyTokens
              }
              allocation {
                supplyAssets
                supplyAssetsUsd
                market {
                  uniqueKey
                  loanAsset {
                    name
                    address
                  }
                  collateralAsset {
                    name
                    address
                  }
                  oracleAddress
                  irmAddress
                  lltv
                  state {
                    rewards {
                      asset {
                        address
                      }
                      supplyApr
                      borrowApr
                    }
                  }
                }
              }
            }
          }
        }
        """ % vault_address
        
        return cls.query_graphql(query)
    
    @classmethod
    def get_rate_at_target(cls, irm_address, market_id):
        """
        Get rateAtTarget from IRM contract
        
        Args:
            irm_address: IRM contract address
            market_id: Market ID (bytes32)
            
        Returns:
            str: rateAtTarget value as a string (in Wei format) or None if failed
        """
        try:
            if irm_address == constants.ADDRESS_ZERO:
                return None
                
            w3 = Web3(Web3.HTTPProvider(cls.RPC_URL))
            irm_contract = w3.eth.contract(
                address=Web3.to_checksum_address(irm_address),
                abi=cls.IRM_ABI
            )
            
            # Call rateAtTarget function with market ID
            rate_at_target = irm_contract.functions.rateAtTarget(market_id).call()
            
            # Return as string (original Wei format)
            return str(rate_at_target)
            
        except Exception as e:
            logger.error(f"Error getting rateAtTarget: {str(e)}")
            return None
    
    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY/APR for Morpho reserve using AdaptiveCurveIRM (same as Ethereum)
        
        Args:
            our_supply: Amount we're planning to supply
            reserve_data: Reserve data dictionary
            
        Returns:
            tuple: (reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr)
            All APY/APR values are returned as percentages
        """
        try:
            total_supplied = float(reserve_data.get('total_supplied', 0))
            total_borrowed = float(reserve_data.get('total_borrowed', 0))
            fee = str(reserve_data.get('fee_percentage', 0))
            rate_per_second = float(reserve_data.get('rate_per_second', 0)) / 1e18
            total_supply_with_ours = total_supplied + our_supply
            utilization = total_borrowed / total_supply_with_ours if total_supply_with_ours > 0 else 0

            # Use AdaptiveCurveIRM to get r_90 from current state
            current_utilization = total_borrowed / total_supplied if total_supplied > 0 else 0
            r_90 = cls.get_r_90(rate_per_second, current_utilization)
            # Calculate new borrow rate for new utilization
            new_rate_per_second = cls.adaptive_curve_borrow_rate(utilization, r_90)
            borrow_apy = cls.calculate_morpho_borrow_apy(new_rate_per_second)
            supply_apr, supply_apy = cls.calculate_morpho_supply_rates(borrow_apy, utilization, fee)

            # TEMPORARILY DISABLED: Rewards APY calculation
            # Set rewards to 0 to calculate total APY only from base APY
            rewards_apr = 0
            rewards_apy = 0
            
            # Original rewards calculation code (commented out for now):
            # if reserve_data.get('yearlySupplyTokens') and reserve_data.get('rewardTokenPriceUsd'):
            #     reward_token_decimals = int(reserve_data.get('rewardTokenDecimals', 18))
            #     yearly_supply_tokens = float(reserve_data['yearlySupplyTokens']) / (10**reward_token_decimals)
            #     reward_price = float(reserve_data['rewardTokenPriceUsd'])
            #     asset_price = float(reserve_data['token_price'])
            #     if total_supply_with_ours > 0:
            #         reward_value = yearly_supply_tokens * reward_price
            #         total_supply_value = total_supply_with_ours * asset_price
            #         rewards_apr = (reward_value / total_supply_value) if total_supply_value > 0 else 0
            #         rewards_apy = math.exp(rewards_apr) - 1 if rewards_apr > 0 else 0
            #     logging.info(f"[Morpho Base] rewards: yearly_supply_tokens={yearly_supply_tokens}, reward_price={reward_price}, asset_price={asset_price}, reward_value={reward_value}, total_supply_value={total_supply_value}, rewards_apr={rewards_apr}, rewards_apy={rewards_apy}")
            
            # Total APY is now only base APY (supply_apy) since rewards are set to 0
            total_apr = supply_apr + rewards_apr
            total_apy = supply_apy + rewards_apy

            return (
                supply_apy,
                rewards_apy,
                total_apy,
                supply_apr,
                rewards_apr,
                total_apr
            )
        except Exception as e:
            logger.error(f"Error calculating reserve APY: {str(e)}")
            return 0, 0, 0, 0, 0, 0
    
    @classmethod
    def calculate_pool_apr_apy(cls, our_supply, pool_data):
        """
        Morpho doesn't have pools in the traditional sense, implementing for compatibility
        """
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        Morpho doesn't have pools in this context, only vaults/reserves
        """
        return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch and process Morpho reserve data
        """
        try:
            address = fund_data.get('address')
            if not address:
                return None
            
            # Check if this is a market unique key (hex string longer than regular address)
            if len(address) > 42:  # Regular address is 42 chars (0x + 40 hex chars)
                # This is a market unique key, fetch market data
                try:
                    market_data = asyncio.run(cls.get_market_by_unique_key(address))
                    if market_data:
                        # Return exactly the same format as /api/morpho-markets
                        return market_data
                    else:
                        logger.error(f"Market with unique key {address} not found")
                        return None
                except Exception as e:
                    logger.error(f"Error fetching market data for unique key {address}: {str(e)}")
                    return None
            else:
                # This is a vault address, use the original vault logic
                vault_address = address
                # Get vault data from GraphQL API
                response = cls.get_vault_data(vault_address)
                if not response or 'data' not in response or 'vaultByAddress' not in response['data']:
                    return None
                    
                vault_data = response['data']['vaultByAddress']
                state = vault_data.get('state', {})
                
                # Process allocation data
                allocations = state.get('allocation', [])
                active_allocations = [alloc for alloc in allocations if alloc.get('supplyAssets', 0) > 0]
                
                # Use the first active allocation if available for market details
                active_market = None
                if active_allocations:
                    active_market = active_allocations[0].get('market', {})
                    
                # If no active allocations, use the first allocation
                if not active_market and allocations:
                    active_market = allocations[0].get('market', {})
                
                # Extract market details
                market_id = active_market.get('uniqueKey') if active_market else None
                loan_asset_name = active_market.get('loanAsset', {}).get('name', 'Unknown') if active_market else 'Unknown'
                irm_address = active_market.get('irmAddress') if active_market else constants.ADDRESS_ZERO
                
                # Get rate at target if market ID is available (as Wei string)
                rate_at_target_wei = None
                if market_id and irm_address and irm_address != constants.ADDRESS_ZERO:
                    rate_at_target_wei = cls.get_rate_at_target(irm_address, market_id)
                
                # Calculate utilization rate
                total_supplied = float(state.get('totalAssets', 0))
                total_borrowed = sum([float(alloc.get('supplyAssets', 0)) for alloc in allocations])
                utilization_rate = total_borrowed / total_supplied if total_supplied > 0 else 0
                
                # Calculate APY with the proper formula if rate_at_target_wei is available
                calculated_apy = 0
                if rate_at_target_wei:
                    # Convert Wei to per-second rate
                    rate_per_second = float(rate_at_target_wei) / 1e18
                    # Use AdaptiveCurveIRM
                    r_90 = cls.get_r_90(rate_per_second, utilization_rate)
                    new_rate_per_second = cls.adaptive_curve_borrow_rate(utilization_rate, r_90)
                    borrow_apy = cls.calculate_morpho_borrow_apy(new_rate_per_second)
                    supply_apr, calculated_apy = cls.calculate_morpho_supply_rates(borrow_apy, utilization_rate, "0")
                
                # Create reserve data in the required format
                reserve_info = {
                    'fee_percentage': 0.0,  # Morpho doesn't have fees at vault level
                    'id': vault_address,
                    'name': f"{loan_asset_name} Vault",
                    'network': cls.NETWORK_NAME.lower(),
                    'optimal_usage_ratio': 0.9,
                    'protocol': cls.PROTOCOL_NAME,
                    'rate_per_second': rate_at_target_wei if rate_at_target_wei else "0",
                    'reserve_factor': 0,
                    'rewardTokenDecimals': 18,
                    'rewardTokenPriceUsd': "0",
                    'source': "Morpho Base",
                    'token_price': 1.0,  # Default for vault
                    'total_borrowed': total_borrowed,
                    'total_supplied': total_supplied,
                    'yearlySupplyTokens': "0"
                }
                
                return reserve_info
        except Exception as e:
            logger.error(f"Error processing Morpho reserve: {str(e)}")
            return None 