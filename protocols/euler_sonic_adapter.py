"""
Euler Protocol Adapter for Sonic network
"""
import logging
import json
from web3 import Web3
from .core.base_protocol import BaseProtocolAdapter

class EulerSonicAdapter(BaseProtocolAdapter):
    SOURCE = "Euler Sonic"
    PROTOCOL_NAME = "Euler"
    RPC_URL = "https://rpc.soniclabs.com"
    NETWORK_NAME = "Sonic"
    @classmethod
    def detect_protocol(cls, fund_data):
        return fund_data.get('source') == cls.SOURCE

    @classmethod
    def get_reserve_abi(cls):
        try:
            with open('protocols/abi/euler_bacon_abi.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading Euler reserve ABI: {e}")
            return None

    @classmethod
    def get_interest_model_abi(cls):
        try:
            with open('protocols/abi/euler_abi.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading Euler interest model ABI: {e}")
            return None

    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        # Euler doesn't have pools in this context, only reserves
        return None

    @classmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        """
        Calculate borrow rate for Euler reserves using IRMLinearKink formula
        
        Args:
            utilization: Current utilization rate (0-1)
            optimal_usage_ratio: Optimal utilization rate (kink) (0-1)
            base_variable_borrow_rate: Base variable borrow rate
            variable_rate_slope1: Slope 1 for variable rate
            variable_rate_slope2: Slope 2 for variable rate
            
        Returns:
            float: Calculated borrow APY
        """
        # Scale down the rates (Euler uses 1e18 for rates)
        scale = 1e18
        base_rate = float(base_variable_borrow_rate) / scale
        slope1 = float(variable_rate_slope1) / scale
        slope2 = float(variable_rate_slope2) / scale
        
        # Scale utilization to match contract's uint32.max scale
        utilization_scaled = utilization * 4294967295  # type(uint32).max
        
        # Start with base rate
        borrow_rate = base_rate
        
        if utilization_scaled <= optimal_usage_ratio * 4294967295:
            # If utilization is below kink, just multiply by slope1
            borrow_rate += utilization_scaled * slope1 / 4294967295
        else:
            # If utilization is above kink:
            # 1. Add kink * slope1
            borrow_rate += (optimal_usage_ratio * 4294967295) * slope1 / 4294967295
            # 2. Add excess utilization * slope2
            excess_utilization = utilization_scaled - (optimal_usage_ratio * 4294967295)
            borrow_rate += excess_utilization * slope2 / 4294967295
        
        return borrow_rate

    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY for Euler reserve
        
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
    def fetch_reserve_data(cls, fund_data, wallet_address):
        try:
            reserve_address = fund_data.get('address')
            if not reserve_address:
                logging.error("[EULER] Missing address in fund_data")
                return None

            w3 = Web3(Web3.HTTPProvider(cls.RPC_URL))
            reserve_address = Web3.to_checksum_address(reserve_address)
            reserve_abi = cls.get_reserve_abi()
            if not reserve_abi:
                logging.error("[EULER] Euler reserve ABI not loaded")
                return None
            reserve_contract = w3.eth.contract(address=reserve_address, abi=reserve_abi)

            # 1. Get interestRateModel address
            try:
                irm_address = reserve_contract.functions.interestRateModel().call()
                irm_address = Web3.to_checksum_address(irm_address)
            except Exception as e:
                return None
            irm_abi = cls.get_interest_model_abi()
            if not irm_abi:
                logging.error("[EULER] Euler interest model ABI not loaded")
                return None
            irm_contract = w3.eth.contract(address=irm_address, abi=irm_abi)

            # 2. Get rates
            try:
                base_rate = irm_contract.functions.baseRate().call()
                kink = irm_contract.functions.kink().call()
                slope1 = irm_contract.functions.slope1().call()
                slope2 = irm_contract.functions.slope2().call()
            except Exception as e:
                logging.error(f"[EULER] Failed to get rates from interest model: {e}")
                return None

            # 3. Get totalAssets and totalBorrows
            try:
                total_assets = reserve_contract.functions.totalAssets().call()
                total_borrows = reserve_contract.functions.totalBorrows().call()
            except Exception as e:
                logging.error(f"[EULER] Failed to get totalAssets/totalBorrows: {e}")
                return None

            # 4. Get decimals and symbol
            try:
                token_contract = w3.eth.contract(address=reserve_address, abi=[
                    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
                    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
                ])
                decimals = token_contract.functions.decimals().call()
                symbol = token_contract.functions.symbol().call()
            except Exception as e:
                decimals = 18
                symbol = "TOKEN"
                logging.warning(f"[EULER] Failed to get decimals/symbol: {e}")

            def scale(value):
                return float(value) / (10 ** decimals)
            
            total_supplied = scale(total_assets)
            total_borrowed = scale(total_borrows)
            
            # Calculate utilization rate
            utilization_rate = total_borrowed / total_supplied if total_supplied > 0 else 0

            reserve_info = {
                "name": f"{symbol} Reserve",
                "protocol": cls.PROTOCOL_NAME,
                "base_variable_borrow_rate": float(base_rate) / 1e18,
                "optimal_usage_ratio": float(kink) / 4294967295,
                "variable_rate_slope1": float(slope1),
                "variable_rate_slope2": float(slope2),
                "total_supplied": total_supplied,
                "total_borrowed": total_borrowed,
                "utilization_rate": utilization_rate,
                "token_price": 1.0,  # Default, adjust if needed
                "fee_percentage": 0.0,  # Euler doesn't have fees in this context
                "reserve_factor": 0.0,  # Add if available
                "rewards_per_year": 0,  # Euler doesn't have rewards in this context
                "source": cls.SOURCE,
                "network": cls.NETWORK_NAME
            }
            return reserve_info

        except Exception as e:
            return None

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