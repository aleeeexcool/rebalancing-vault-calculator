"""
AAVE Protocol Adapter for Base network
"""
import json
import os
import logging
from web3 import Web3
from .core.base_protocol import BaseProtocolAdapter
from .core.utils import get_token_price

class AaveBaseAdapter(BaseProtocolAdapter):
    """
    Adapter for AAVE protocol on Base network
    """
    
    SOURCE = "Aave Base"
    PROTOCOL_NAME = "Aave"
    NETWORK_NAME = "Base"
    RPC_URL = "https://mainnet.base.org"
    AAVE_CONTRACT_ADDRESS = "0xC4Fcf9893072d61Cc2899C0054877Cb752587981"  # Main AAVE contract on Base
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund is from AAVE protocol
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
        Fetch and process AAVE reserve data
        """
        try:
            token_address = fund_data.get('address')
            if not token_address:
                return None
                
            token_address = Web3.to_checksum_address(token_address)
            aave_contract_address = Web3.to_checksum_address(cls.AAVE_CONTRACT_ADDRESS)
            w3 = Web3(Web3.HTTPProvider(cls.RPC_URL))
            aave_abi = cls.get_aave_abi()
            
            if not aave_abi:
                return None
                
            aave_contract = w3.eth.contract(
                address=aave_contract_address,
                abi=aave_abi
            )
            
            # Always get the rate strategy address dynamically for each token
            try:
                rate_strategy_address = aave_contract.functions.getInterestRateStrategyAddress(token_address).call()
                rate_strategy_address = Web3.to_checksum_address(rate_strategy_address)
            except Exception as e:
                logging.error(f"Could not get interest rate strategy address for {token_address}: {str(e)}")
                return None
            
            reserve_abi = cls.get_reserve_abi()
            
            if not reserve_abi:
                return None
                
            rate_strategy_contract = w3.eth.contract(
                address=rate_strategy_address,
                abi=reserve_abi
            )
            
            try:
                config_data = aave_contract.functions.getReserveConfigurationData(token_address).call()
            except Exception:
                return None
                
            try:
                reserve_data = aave_contract.functions.getReserveData(token_address).call()
            except Exception:
                return None
                
            try:
                interest_rate_data = rate_strategy_contract.functions.getInterestRateDataBps(token_address).call()
            except Exception:
                return None
                
            def bps_to_percent(bps_value):
                return float(bps_value) / 100
                
            def from_wei(value):
                return float(w3.from_wei(value, 'wei'))
                
            token_name = "USDC"
            decimals = 18
            try:
                token_contract = w3.eth.contract(address=token_address, abi=[
                    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
                    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
                ])
                decimals = token_contract.functions.decimals().call()
                token_name = token_contract.functions.symbol().call()
            except Exception:
                pass
                
            def scale_token_amount(value):
                return float(value) / (10 ** decimals)
                
            # Get unbacked value from reserve data
            unbacked = scale_token_amount(reserve_data[0])  # First item in reserve_data is unbacked
                
            total_borrowed = scale_token_amount(reserve_data[4])
            total_supplied = scale_token_amount(reserve_data[2])
            
            # Calculate utilization rate
            utilization_rate = total_borrowed / total_supplied if total_supplied > 0 else 0
                
            reserve_info = {
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