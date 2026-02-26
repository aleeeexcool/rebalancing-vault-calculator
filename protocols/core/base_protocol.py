"""
Base Protocol Adapter - Abstract class for all protocol adapters
"""
from abc import ABC, abstractmethod
import logging

class BaseProtocolAdapter(ABC):
    """
    Abstract base class for protocol adapters.
    Any new protocol should implement this interface.
    """
    
    @classmethod
    @abstractmethod
    def detect_protocol(cls, fund_data):
        """
        Determine if this adapter can process the given fund data
        
        Args:
            fund_data (dict): Fund data with address and other info
            
        Returns:
            bool: True if this adapter can process this fund, False otherwise
        """
        pass
    
    @classmethod
    @abstractmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        """
        Fetch and process pool data
        
        Args:
            fund_data (dict): Fund data with address and other info
            wallet_address (str): User's wallet address
            
        Returns:
            dict: Processed pool data 
            None: If failed to process
        """
        pass
    
    @classmethod
    @abstractmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        """
        Fetch and process reserve data
        
        Args:
            fund_data (dict): Fund data with address and other info
            wallet_address (str): User's wallet address
            
        Returns:
            dict: Processed reserve data
            None: If failed to process
        """
        pass
    
    @classmethod
    @abstractmethod
    def calculate_borrow_rate(cls, utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
        """
        Calculate borrow rate for protocol reserves
        
        Args:
            utilization: Current utilization rate
            optimal_usage_ratio: Optimal utilization rate
            base_variable_borrow_rate: Base variable borrow rate
            variable_rate_slope1: First slope for borrow rate curve
            variable_rate_slope2: Second slope for borrow rate curve
            
        Returns:
            float: Calculated borrow APY
        """
        pass
    
    @classmethod
    @abstractmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY for protocol reserve
        
        Args:
            our_supply: Amount we're planning to supply
            reserve_data: Reserve data dictionary
            
        Returns:
            tuple: (reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr)
        """
        pass 