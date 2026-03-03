"""
Morpho Protocol Adapter for Ethereum network
"""
import logging
import math
from .core.base_protocol import BaseProtocolAdapter

class MorphoEthereumAdapter(BaseProtocolAdapter):
    """
    Adapter for Morpho protocol on Ethereum network
    """
    
    SOURCE = "Morpho ethereum"
    PROTOCOL_NAME = "Morpho"
    NETWORK_NAME = "ethereum"
    SECONDS_IN_YEAR = 31536000
    TARGET_UTILIZATION = 0.9  # 90% target utilization
    CURVE_STEEPNESS = 4  # Fixed parameter that determines the steepness of the curve
    
    @classmethod
    def detect_protocol(cls, fund_data):
        """
        Detect if the fund data is for Morpho protocol
        """
        return fund_data.get('source', '').lower() == cls.SOURCE.lower()
    
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
        logging.info(f"[Morpho] get_r_90: current_rate_per_second={current_rate_per_second}, current_utilization={current_utilization}, error={error}, curve={curve}, r_90={r_90}")
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
        logging.info(f"[Morpho] adaptive_curve_borrow_rate: utilization={utilization}, r_90={r_90}, error={error}, curve={curve}, rate={rate}")
        return rate

    @classmethod
    def calculate_morpho_borrow_apy(cls, rate_per_second):
        """
        Calculate borrow APY using the exponential formula: exp(rate * seconds) - 1
        
        Args:
            rate_per_second: Interest rate per second in Wei (18 decimals)
            
        Returns:
            float: The borrow APY as a percentage
        """
        try:
            rate = float(rate_per_second)  # Already in per-second units
            apy = math.exp(rate * cls.SECONDS_IN_YEAR) - 1
            logging.info(f"[Morpho] calculate_morpho_borrow_apy: rate_per_second={rate_per_second}, apy={apy}")
            if apy > 1:
                logging.warning(f"Unusually high APY calculated: {apy} for rate {rate_per_second}")
                return 0
            return apy
        except Exception as e:
            logging.error(f"Error calculating Morpho borrow APY: {str(e)}")
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
            logging.info(f"[Morpho] calculate_morpho_supply_rates: borrow_apy={borrow_apy}, utilization={utilization}, fee_decimal={fee_decimal}, supply_apy={supply_apy}, supply_apr={supply_apr}")
            return supply_apr, supply_apy
        except Exception as e:
            logging.error(f"Error calculating Morpho supply rates: {str(e)}")
            return 0, 0
    
    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY/APR for Morpho reserve
        
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
            logging.info(f"[Morpho] calculate_reserve_apy: our_supply={our_supply}, total_supplied={total_supplied}, total_borrowed={total_borrowed}, utilization={utilization}, r_90={r_90}, new_rate_per_second={new_rate_per_second}, borrow_apy={borrow_apy}, supply_apr={supply_apr}, supply_apy={supply_apy}")
            rewards_apr = 0
            rewards_apy = 0
            if reserve_data.get('yearlySupplyTokens') and reserve_data.get('rewardTokenPriceUsd'):
                reward_token_decimals = int(reserve_data.get('rewardTokenDecimals', 18))
                yearly_supply_tokens = float(reserve_data['yearlySupplyTokens']) / (10**reward_token_decimals)
                reward_price = float(reserve_data['rewardTokenPriceUsd'])
                asset_price = float(reserve_data['token_price'])
                if total_supply_with_ours > 0:
                    reward_value = yearly_supply_tokens * reward_price
                    total_supply_value = total_supply_with_ours * asset_price
                    rewards_apr = (reward_value / total_supply_value) if total_supply_value > 0 else 0
                    rewards_apy = math.exp(rewards_apr) - 1 if rewards_apr > 0 else 0
                logging.info(f"[Morpho] rewards: yearly_supply_tokens={yearly_supply_tokens}, reward_price={reward_price}, asset_price={asset_price}, reward_value={reward_value}, total_supply_value={total_supply_value}, rewards_apr={rewards_apr}, rewards_apy={rewards_apy}")
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
            logging.error(f"Error calculating reserve APY: {str(e)}")
            return 0, 0, 0, 0, 0, 0
    
    @classmethod
    def calculate_pool_apr_apy(cls, our_supply, pool_data):
        """
        Calculate APR/APY for Morpho pool
        
        Args:
            our_supply: Amount we're planning to supply
            pool_data: Pool data dictionary
            
        Returns:
            tuple: (pool_apy, rewards_apy, total_apy, pool_apr, rewards_apr, total_apr)
            All APY/APR values are returned as percentages
        """
        return cls.calculate_reserve_apy(our_supply, pool_data)
    
    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        Morpho doesn't have pools in this context, implementing for compatibility
        """
        return None
    
    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch reserve data for Morpho protocol
        """
        return None 