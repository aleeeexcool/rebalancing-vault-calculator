"""
Moonwell Protocol Adapter for Base network
"""
import json
import os
import logging
from web3 import Web3
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_token_price

class MoonwellBaseAdapter(BaseProtocolAdapter):
    """
    Adapter for Moonwell protocol on Base network
    """
    
    SOURCE = "Moonwell Base"
    PROTOCOL_NAME = "Moonwell"
    NETWORK_NAME = "Base"
    RPC_URL = "https://base.publicnode.com"
    # Alternative RPCs to try if the main one fails
    ALTERNATIVE_RPCS = [
        "https://base.llamarpc.com",
        "https://1rpc.io/base",
        "https://base.meowrpc.com"
    ]
    
    # Constants for calculations
    MANTISSA_PRECISION = 10**18
    SECONDS_PER_YEAR = 31536000  # 365 days
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from Moonwell protocol
        """
        # Check in source field
        if fund_data.get('source') == cls.SOURCE:
            return True
        return False
    
    @classmethod
    def get_token_abi(cls):
        """
        Load the Moonwell mToken ABI from file
        """
        try:
            with open('protocols/abi/moonwell_token_abi.json', 'r') as f:
                return json.load(f)
        except Exception:
            # Return None if file not found
            return None
    
    @classmethod
    def get_irm_abi(cls):
        """
        Load the Interest Rate Model ABI from file
        """
        try:
            with open('protocols/abi/moonwell_irm_abi.json', 'r') as f:
                return json.load(f)
        except Exception:
            # Return None if file not found
            return None
    
    @classmethod
    def calculate_utilization_rate(cls, cash, borrows, reserves):
        """
        Calculate utilization rate using the formula: borrows / (cash + borrows - reserves)
        """
        if borrows == 0:
            return 0
        
        denominator = cash + borrows - reserves
        if denominator <= 0:
            return 0
            
        return borrows / denominator
    
    @classmethod
    def calculate_borrow_rate(cls, utilization, kink, base_rate, multiplier, jump_multiplier):
        """
        Calculate borrow rate based on JumpRateModel
        
        Args:
            utilization: Current utilization rate (0-1)
            kink: The utilization point at which the jump multiplier is applied
            base_rate: Base interest rate
            multiplier: Multiplier for the normal rate
            jump_multiplier: Multiplier after reaching the kink
            
        Returns:
            float: Calculated borrow APY
        """
        if utilization <= kink:
            # Normal rate calculation
            return base_rate + (utilization * multiplier)
        else:
            # Calculate with jump multiplier for utilization above kink
            normal_rate = base_rate + (kink * multiplier)
            excess_utilization = utilization - kink
            excess_utilization_rate = excess_utilization / (1 - kink)
            return normal_rate + (excess_utilization_rate * jump_multiplier)
    
    @classmethod
    def calculate_supply_rate(cls, utilization, borrow_rate, reserve_factor):
        """
        Calculate supply rate: utilizationRate * borrowRate * (1 - reserveFactor)
        """
        return utilization * borrow_rate * (1 - reserve_factor)
    
    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY for Moonwell reserve based on the JumpRateModel
        
        Args:
            our_supply: Amount we're planning to supply
            reserve_data: Reserve data dictionary
            
        Returns:
            tuple: (reserve_apy, rewards_apy, total_apy, base_apr, rewards_apr, total_apr)
        """
        total_supplied = reserve_data.get('total_supplied', 0) + our_supply
        total_borrowed = reserve_data.get('total_borrowed', 0)
        reserves = reserve_data.get('total_reserves', 0)
        
        # If there's no supply, we can't calculate utilization
        if total_supplied <= 0:
            return 0, 0, 0, 0, 0, 0
            
        # Calculate utilization rate
        utilization = cls.calculate_utilization_rate(
            total_supplied - total_borrowed,  # cash = supplied - borrowed
            total_borrowed,
            reserves
        )
        
        # Get model parameters
        kink = reserve_data.get('kink', 0.8)
        base_rate = reserve_data.get('base_rate', 0)
        multiplier = reserve_data.get('multiplier', 0.05)
        jump_multiplier = reserve_data.get('jump_multiplier', 0.8)
        reserve_factor = reserve_data.get('reserve_factor', 0.1)
        
        # Calculate borrow rate
        borrow_rate = cls.calculate_borrow_rate(
            utilization,
            kink,
            base_rate,
            multiplier,
            jump_multiplier
        )
        
        # Calculate supply rate 
        supply_apy = cls.calculate_supply_rate(
            utilization,
            borrow_rate,
            reserve_factor
        )
        
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
        Moonwell doesn't have pools in this context, implementing for compatibility
        """
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        Moonwell doesn't have pools in this context, only reserves
        """
        return None
    
    @classmethod
    def _get_web3_provider(cls):
        """
        Get a Web3 provider, trying alternative RPCs if the main one fails
        """
        # First try the main RPC
        providers = [cls.RPC_URL] + cls.ALTERNATIVE_RPCS
        
        for provider_url in providers:
            try:
                w3 = Web3(Web3.HTTPProvider(provider_url))
                # Test the connection
                w3.eth.block_number
                logging.info(f"Connected to RPC: {provider_url}")
                return w3
            except Exception as e:
                logging.warning(f"Failed to connect to RPC {provider_url}: {str(e)}")
                continue
                
        logging.error("All RPC endpoints failed")
        return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch and process Moonwell reserve data
        """
        try:
            token_address = fund_data.get('address')
            if not token_address:
                return None
                
            token_address = Web3.to_checksum_address(token_address)
            w3 = cls._get_web3_provider()
            
            if not w3:
                logging.error("Could not connect to any RPC endpoint")
                return None
                
            token_abi = cls.get_token_abi()
            irm_abi = cls.get_irm_abi()
            
            if not token_abi or not irm_abi:
                logging.error(f"Failed to load ABIs for Moonwell")
                return None
                
            # Connect to the mToken contract
            token_contract = w3.eth.contract(
                address=token_address,
                abi=token_abi
            )
            
            # Get token information
            try:
                token_name = token_contract.functions.name().call()
                token_symbol = token_contract.functions.symbol().call()
                underlying_address = token_contract.functions.underlying().call()
                total_supply = token_contract.functions.totalSupply().call() / 100000000
                total_borrows = token_contract.functions.totalBorrows().call()
                total_reserves = token_contract.functions.totalReserves().call()
                reserve_factor = token_contract.functions.reserveFactorMantissa().call() / cls.MANTISSA_PRECISION
                cash = token_contract.functions.getCash().call()
                exchange_rate_stored = token_contract.functions.exchangeRateStored().call()
                mtoken_decimals = token_contract.functions.decimals().call()
                # Get the interest rate model address
                irm_address = token_contract.functions.interestRateModel().call()
                logging.info(f"Retrieved interest rate model address for {token_symbol}: {irm_address}")
            except Exception as e:
                logging.error(f"Error fetching token data for {token_address}: {str(e)}")
                return None
                
            # Connect to the interest rate model contract
            irm_contract = w3.eth.contract(
                address=irm_address,
                abi=irm_abi
            )
            
            # Get interest rate model parameters
            try:
                base_rate_per_timestamp = irm_contract.functions.baseRatePerTimestamp().call()
                multiplier_per_timestamp = irm_contract.functions.multiplierPerTimestamp().call()
                jump_multiplier_per_timestamp = irm_contract.functions.jumpMultiplierPerTimestamp().call()
                kink = irm_contract.functions.kink().call() / cls.MANTISSA_PRECISION
                
                # Convert per-timestamp rates to annual rates
                base_rate = base_rate_per_timestamp * cls.SECONDS_PER_YEAR / cls.MANTISSA_PRECISION
                multiplier = multiplier_per_timestamp * cls.SECONDS_PER_YEAR / cls.MANTISSA_PRECISION
                jump_multiplier = jump_multiplier_per_timestamp * cls.SECONDS_PER_YEAR / cls.MANTISSA_PRECISION
            except Exception as e:
                logging.error(f"Error fetching interest rate model data for {token_address}: {str(e)}")
                return None
                
            # Get token decimals for scaling
            underlying_decimals = 18  # Default to 18 if we can't get the value
            try:
                if underlying_address != '0x0000000000000000000000000000000000000000':
                    # For non-ETH tokens
                    underlying_contract = w3.eth.contract(address=underlying_address, abi=[
                        {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
                        {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
                    ])
                    underlying_decimals = underlying_contract.functions.decimals().call()
                    underlying_symbol = underlying_contract.functions.symbol().call()
                else:
                    # For ETH
                    underlying_symbol = "ETH"
            except Exception:
                underlying_symbol = token_symbol.replace("m", "")  # Remove "m" prefix if it exists
            # Calculate supplied in underlying tokens
            try:
                supplied = (total_supply * exchange_rate_stored) / (10 ** (18 + underlying_decimals - mtoken_decimals))
            except Exception as e:
                logging.error(f"Error calculating supplied: {str(e)}")
                supplied = 0
            def scale_token_amount(value, decimals):
                return float(value) / (10 ** decimals)
            total_supplied_scaled = float(supplied)
            total_borrows_scaled = scale_token_amount(total_borrows, underlying_decimals)
            total_reserves_scaled = scale_token_amount(total_reserves, underlying_decimals)
            cash_scaled = scale_token_amount(cash, underlying_decimals)
                
            # Compute current utilization and rates
            utilization = cls.calculate_utilization_rate(cash_scaled, total_borrows_scaled, total_reserves_scaled)
            borrow_rate = cls.calculate_borrow_rate(utilization, kink, base_rate, multiplier, jump_multiplier)
            supply_rate = cls.calculate_supply_rate(utilization, borrow_rate, reserve_factor)
                
            reserve_info = {
                'name': f"{underlying_symbol} Reserve",
                'protocol': cls.PROTOCOL_NAME,
                'total_supplied': total_supplied_scaled,
                'total_borrowed': total_borrows_scaled,
                'total_reserves': total_reserves_scaled,
                'cash': cash_scaled,
                'utilization': utilization,
                'utilization_rate': utilization,  # Add utilization_rate field (duplicate of utilization)
                'kink': kink,
                'base_rate': base_rate,
                'multiplier': multiplier,
                'jump_multiplier': jump_multiplier,
                'reserve_factor': reserve_factor,
                'current_borrow_rate': borrow_rate,
                'current_supply_rate': supply_rate,
                'token_price': 1.0,  # Default price, should be updated with actual price
                'source': cls.SOURCE,
                'network': cls.NETWORK_NAME,
                'rewards_per_year': 0,  # Default value, should be updated with actual rewards
                'type': 'reserve'
            }
            
            return reserve_info
        except Exception as e:
            logging.error(f"Error processing Moonwell reserve on Base: {str(e)}")
            return None 