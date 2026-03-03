"""
Protocol Registry - Adapter for ProtocolFactory
This module provides a bridge between our new adapter pattern and the existing ProtocolFactory
"""
import logging
from typing import Dict, Type, Optional
from .core.base_protocol import BaseProtocolAdapter
from .core.protocol_factory import ProtocolFactory
from .lendle_mantle_adapter import LendleMantleAdapter
from .init_mantle_adapter import InitAdapter
from .merchant_moe_mantle_adapter import MerchantMoeAdapter
from .euler_sonic_adapter import EulerSonicAdapter
from .aave_sonic_adapter import AaveSonicAdapter
from .aave_base_adapter import AaveBaseAdapter
from .moonwell_base_adapter import MoonwellBaseAdapter
from .morpho_base_adapter import MorphoBaseAdapter
from .lendle_mantle_adapter_v2 import LendleMantleAdapterV2
from .lendle_isolated_mantle_adapter import LendleMantleIsolatedAdapter
from .lendle_mantle_adapter_v3 import LendleMantleAdapterV3
from .init_mantle_adapter_v3 import InitMantleAdapterV3

class ProtocolRegistry:
    """
    Registry for all protocol adapters.
    Provides methods to get the appropriate adapter for a given protocol.
    This class serves as a bridge between the new adapter pattern and the existing ProtocolFactory.
    """
    
    @classmethod
    def get_adapter_by_name(cls, protocol_name: str) -> Optional[Type[BaseProtocolAdapter]]:
        """
        Get protocol adapter by protocol name
        
        Args:
            protocol_name: Name of the protocol
            
        Returns:
            BaseProtocolAdapter: Protocol adapter class if found, None otherwise
        """
        # Create a test fund data with the source field
        fund_data = {'source': protocol_name, 'type': 'reserve', 'address': '0x'}
        return ProtocolFactory.get_protocol_adapter(fund_data)
    
    @classmethod
    def get_adapter_for_fund(cls, fund_data) -> Optional[Type[BaseProtocolAdapter]]:
        """
        Get appropriate protocol adapter for given fund data
        
        Args:
            fund_data: Fund data with protocol information
            
        Returns:
            BaseProtocolAdapter: Protocol adapter class if found, None otherwise
        """
        return ProtocolFactory.get_protocol_adapter(fund_data)
    
    @classmethod
    def calculate_investment_metrics(cls, our_supply, investment):
        """
        Calculate investment metrics using the appropriate protocol adapter
        
        Args:
            our_supply: Amount we're planning to supply
            investment: Investment data dictionary
            
        Returns:
            tuple: Investment metrics (depends on investment type)
                - For reserves: (reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr)
                - For pools: (base_apy, rewards_apy, total_apy, base_apr, rewards_apr, total_apr)
        """
        adapter = cls.get_adapter_for_fund(investment)
        
        if not adapter:
            # Default to Lendle if no adapter found
            logging.warning(f"No adapter found for investment {investment}, using Lendle adapter")
            adapter = LendleMantleAdapter
        
        if investment['type'] == 'reserve':
            # Calculate utilization rate if not already present
            if 'utilization_rate' not in investment and investment.get('total_supplied', 0) > 0:
                # Skip this calculation for Init protocol as it's handled differently
                if 'Init' not in investment.get('source', ''):
                    investment['utilization_rate'] = investment.get('total_borrowed', 0) / investment.get('total_supplied', 0)
            
            # Return APY metrics
            return adapter.calculate_reserve_apy(our_supply, investment)
        elif investment['type'] == 'pool':
            # For pool types, use the dedicated pool methods (if any)
            if hasattr(adapter, 'calculate_pool_apr_apy'):
                return adapter.calculate_pool_apr_apy(our_supply, investment)
            else:
                # Could implement a default pool calculation here if needed
                from .merchant_moe_mantle_adapter import MerchantMoeAdapter
                return MerchantMoeAdapter.calculate_pool_apr_apy(our_supply, investment)
        