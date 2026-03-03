"""
Protocol Factory - Selects appropriate protocol adapter for a given fund
"""
import logging
from ..lendle_mantle_adapter import LendleMantleAdapter
from ..init_mantle_adapter import InitAdapter
from ..merchant_moe_mantle_adapter import MerchantMoeAdapter
from ..aave_sonic_adapter import AaveSonicAdapter
from ..euler_sonic_adapter import EulerSonicAdapter
from ..silo_sonic_adapter import SiloSonicAdapter
from ..aave_base_adapter import AaveBaseAdapter
from ..moonwell_base_adapter import MoonwellBaseAdapter
from ..morpho_base_adapter import MorphoBaseAdapter
from ..morpho_ethereum_adapter import MorphoEthereumAdapter
from ..morpho_hyperevm_adapter import MorphoHyperEvmAdapter
from ..lendle_mantle_adapter_v2 import LendleMantleAdapterV2
from ..lendle_mantle_adapter_v3 import LendleMantleAdapterV3
from ..lendle_isolated_mantle_adapter import LendleMantleIsolatedAdapter
from ..aave_sonic_adapter_v3 import AaveSonicAdapterV3
from ..aave_base_adapter_v3 import AaveBaseAdapterV3
from ..hypurrfi_hyperevm import HypurrFiHyperEvmAdapter
from ..init_mantle_adapter_v3 import InitMantleAdapterV3


class ProtocolFactory:
    """
    Factory class for creating protocol adapters
    """
    
    # List of all available protocol adapters
    _protocol_adapters = [
        LendleMantleAdapter,
        InitAdapter,
        MerchantMoeAdapter,
        AaveSonicAdapter,
        EulerSonicAdapter,
        SiloSonicAdapter,
        AaveBaseAdapter,
        MoonwellBaseAdapter,
        MorphoBaseAdapter,
        MorphoEthereumAdapter,
        MorphoHyperEvmAdapter,
        LendleMantleAdapterV2,
        LendleMantleIsolatedAdapter,
        LendleMantleAdapterV3,
        AaveBaseAdapterV3,
        AaveSonicAdapterV3,
        HypurrFiHyperEvmAdapter,
        InitMantleAdapterV3
    ]
    
    @classmethod
    def get_protocol_adapter(cls, fund_data):
        """
        Determine the appropriate protocol adapter for a given fund
        
        Args:
            fund_data (dict): Fund data with type, address, and possibly source
            
        Returns:
            BaseProtocolAdapter: The appropriate protocol adapter or None if no suitable adapter found
        """
        for adapter in cls._protocol_adapters:
            if adapter.detect_protocol(fund_data):
                return adapter
                
        # No suitable adapter found
        logging.warning(f"No suitable protocol adapter found for fund: {fund_data}")
        return None
    
    @classmethod
    def process_fund(cls, fund_data, wallet_address):
        """
        Process a fund using the appropriate protocol adapter
        
        Args:
            fund_data (dict): Fund data with type, address, and possibly source
            wallet_address (str): User's wallet address
            
        Returns:
            tuple: (data_type, processed_data) where data_type is 'pool' or 'reserve'
            None: If processing failed
        """
        try:
            if not fund_data or 'type' not in fund_data or 'address' not in fund_data:
                logging.error(f"Invalid fund data: {fund_data}")
                return None
                
            adapter = cls.get_protocol_adapter(fund_data)
            
            if not adapter:
                logging.warning(f"No suitable protocol adapter found for fund: {fund_data}")
                return None
                
            fund_type = fund_data.get('type')
            fund_address = fund_data.get('address', 'unknown')
            
            logging.info(f"Processing fund type '{fund_type}' with address '{fund_address[:10]}...' using adapter {adapter.__name__}")
            
            if fund_type == 'pool':
                result = adapter.fetch_pool_data(fund_data, wallet_address)
                if result:
                    logging.info(f"Successfully fetched pool data for {fund_address[:10]}...")
                    return ('pool', result)
                else:
                    logging.error(f"Failed to fetch pool data for {fund_address[:10]}... using adapter {adapter.__name__}")
            elif fund_type == 'reserve':
                result = adapter.fetch_reserve_data(fund_data, wallet_address)
                if result:
                    logging.info(f"Successfully fetched reserve data for {fund_address[:10]}...")
                    return ('reserve', result)
                else:
                    logging.error(f"Failed to fetch reserve data for {fund_address[:10]}... using adapter {adapter.__name__}")
            else:
                logging.error(f"Unknown fund type '{fund_type}' for fund {fund_address[:10]}...")
            
            # Failed to process
            logging.warning(f"Fund processing failed for {fund_address[:10]}... (type: {fund_type})")
            return None
            
        except Exception as e:
            logging.error(f"Exception in process_fund for fund {fund_data.get('address', 'unknown')[:10]}...: {str(e)}")
            return None
        
    @classmethod
    def register_adapter(cls, adapter):
        """
        Register a new protocol adapter
        """
        if adapter not in cls._protocol_adapters:
            cls._protocol_adapters.append(adapter)
    
    @classmethod
    def unregister_adapter(cls, adapter):
        """
        Unregister a protocol adapter
        """
        if adapter in cls._protocol_adapters:
            cls._protocol_adapters.remove(adapter) 