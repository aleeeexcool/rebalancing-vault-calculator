"""
Morpho Markets Data Fetcher
Handles fetching and transforming market data from Morpho's GraphQL API
"""
import json
import logging
import os
from typing import Dict, List, Optional
import aiohttp
from web3 import Web3
from .morpho_ethereum_adapter import MorphoEthereumAdapter
from .network_config import NETWORK_CONFIG

# Set up logger
logger = logging.getLogger(__name__)

# Constants
MORPHO_API_URL = "https://api.morpho.org/graphql"

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

class UnsupportedLoanAssetError(Exception):
    """Raised when the loan asset is not supported in the configuration"""
    pass

async def fetch_morpho_markets(
    first: int = 100,
    loan_asset_address: str = None,
    order_by: str = None,
    unique_keys: List[str] = None
) -> List[Dict]:
    """
    Fetch market data from Morpho's GraphQL API
    
    Args:
        first: Number of markets to fetch
        loan_asset_address: Filter by loan asset address (optional)
        order_by: Order by field (optional)
        unique_keys: List of unique keys to filter by (optional)
        
    Returns:
        List of market data dictionaries
        
    Raises:
        UnsupportedLoanAssetError: If the loan asset is not supported
    """
    try:
        if loan_asset_address and loan_asset_address not in NETWORK_CONFIG:
            raise UnsupportedLoanAssetError(f"Loan asset {loan_asset_address} is not supported")

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
                MORPHO_API_URL,
                json={"query": MARKETS_QUERY, "variables": variables}
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

def transform_market_data(market: Dict) -> Dict:
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
        
        # Get rewards info
        rewards = state.get("rewards", [])
        yearly_supply_tokens = "0"
        reward_token_decimals = 18
        reward_token_price = "0"
        
        if rewards:
            reward = rewards[0]
            yearly_supply_tokens = reward["yearlySupplyTokens"]
            reward_token_decimals = int(reward["asset"]["decimals"])
            reward_token_price = str(reward["asset"]["priceUsd"])
        
        # Build name as in TS: "WBTC/USDC Market" or "USDC Market" if no collateral
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
            "network": loan_asset["chain"]["network"],
            "protocol": "Morpho",
            "source": "Morpho ethereum",
            "token_price": loan_asset["priceUsd"],
            "total_supplied": total_supplied,
            "total_borrowed": total_borrowed,
            "fee_percentage": float(state["fee"]) / 1e18,
            "optimal_usage_ratio": MorphoEthereumAdapter.TARGET_UTILIZATION,
            "reserve_factor": 0,  # Morpho doesn't use reserve factor
            "yearlySupplyTokens": yearly_supply_tokens,
            "rewardTokenDecimals": reward_token_decimals,
            "rewardTokenPriceUsd": reward_token_price,
            "rate_per_second": "0"  # Will be updated with contract call
        }
    except Exception as e:
        logger.error(f"Error transforming market data: {str(e)}")
        return None

async def get_borrow_rate_from_contract(market: Dict) -> Optional[str]:
    """
    Get borrow rate from the interest rate model contract
    
    Args:
        market: Market data dictionary
        
    Returns:
        Borrow rate per second as a string, or None if failed
    """
    try:
        loan_asset_address = market["loanAsset"]["address"]
        if loan_asset_address not in NETWORK_CONFIG:
            raise UnsupportedLoanAssetError(f"Loan asset {loan_asset_address} is not supported")

        network_config = NETWORK_CONFIG[loan_asset_address]
        w3 = Web3(Web3.HTTPProvider(network_config["rpc_url"]))
        
        # Verify RPC connection
        if not w3.is_connected():
            raise Exception(f"Failed to connect to RPC: {network_config['rpc_url']}")
        
        # Load ABI
        abi_path = os.path.join(os.path.dirname(__file__), network_config["abi_path"])
        with open(abi_path, "r") as f:
            abi = json.load(f)
        
        # Create contract instance
        contract_address = Web3.to_checksum_address(network_config["irm_contract"])
        contract = w3.eth.contract(
            address=contract_address,
            abi=abi
        )
        
        # Prepare market params
        market_params = {
            "loanToken": Web3.to_checksum_address(market["loanAsset"]["address"]),
            "collateralToken": Web3.to_checksum_address(market["collateralAsset"]["address"]),
            "oracle": Web3.to_checksum_address(market["oracleAddress"]),
            "irm": Web3.to_checksum_address(market["irmAddress"]),
            "lltv": int(market["lltv"])
        }
        
        # Prepare market state
        market_state = {
            "totalSupplyAssets": int(market["state"]["supplyAssets"]),
            "totalSupplyShares": int(market["state"]["supplyShares"]),
            "totalBorrowAssets": int(market["state"]["borrowAssets"]),
            "totalBorrowShares": int(market["state"]["borrowShares"]),
            "lastUpdate": int(market["state"]["timestamp"]),
            "fee": int(market["state"]["fee"])
        }
        
        # Call contract
        try:
            rate = contract.functions.borrowRateView(market_params, market_state).call()
            return str(rate)
        except Exception as contract_error:
            logger.error(f"Contract call failed: {str(contract_error)}")
            # Try to get more details about the contract
            try:
                code = w3.eth.get_code(contract_address)
                if not code:
                    logger.error(f"No contract code found at address {contract_address}")
                else:
                    logger.error(f"Contract exists at {contract_address} but call failed")
            except Exception as e:
                logger.error(f"Failed to check contract code: {str(e)}")
            raise
        
    except Exception as e:
        logger.error(f"Error getting borrow rate from contract: {str(e)}")
        return None

async def get_morpho_markets_data(
    first: int = 100,
    loan_asset_address: str = None,
    order_by: str = None,
    unique_keys: List[str] = None
) -> List[Dict]:
    """
    Main function to fetch and transform all Morpho market data
    
    Args:
        first: Number of markets to fetch
        loan_asset_address: Filter by loan asset address (optional)
        order_by: Order by field (optional)
        unique_keys: List of unique keys to filter by (optional)
    Returns:
        List of transformed market data dictionaries
    """
    try:
        # Fetch markets
        markets = await fetch_morpho_markets(first, loan_asset_address, order_by, unique_keys)
        if not markets:
            return []
        
        # Transform and enrich market data
        transformed_markets = []
        for market in markets:
            transformed = transform_market_data(market)
            if transformed:
                # Get borrow rate
                rate = await get_borrow_rate_from_contract(market)
                if rate:
                    transformed["rate_per_second"] = rate
                transformed_markets.append(transformed)
        
        return transformed_markets
    except Exception as e:
        logger.error(f"Error getting Morpho markets data: {str(e)}")
        return [] 