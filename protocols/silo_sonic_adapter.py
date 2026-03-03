"""
Silo Protocol Adapter for Sonic network
"""
import logging
import json
from web3 import Web3
from .core.base_protocol import BaseProtocolAdapter

class SiloSonicAdapter(BaseProtocolAdapter):
    SOURCE = "Silo Sonic"
    PROTOCOL_NAME = "Silo"
    NETWORK_NAME = "Sonic"
    RPC_URL = "https://rpc.soniclabs.com"
    SILO_LENS_ADDRESS = "0x0b3f8e6d9aa88ce5d40238690d6903a90c6acac2"

    @classmethod
    def detect_protocol(cls, fund_data):
        return fund_data.get('source') == cls.SOURCE

    @classmethod
    def get_silo_lens_abi(cls):
        try:
            with open('protocols/abi/silo_lens_abi.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading Silo Lens ABI: {e}")
            return None

    @classmethod
    def get_silo_abi(cls):
        try:
            with open('protocols/abi/silo_abi.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading Silo ABI: {e}")
            return None

    @classmethod
    def get_silo_config_abi(cls):
        try:
            with open('protocols/abi/silo_config_abi.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading Silo Config ABI: {e}")
            return None

    @classmethod
    def get_irm_config_abi(cls):
        try:
            with open('protocols/abi/silo_irm_cinfig_abi.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading Silo IRM Config ABI: {e}")
            return None

    @classmethod
    def fetch_pool_data(cls, fund_data, wallet_address):
        # Silo doesn't have pools in this context
        return None

    @classmethod
    def fetch_reserve_data(cls, fund_data, wallet_address):
        try:
            silo_address = fund_data.get('address')
            if not silo_address:
                logging.error("[SILO] Missing address in fund_data")
                return None

            w3 = Web3(Web3.HTTPProvider(cls.RPC_URL))
            silo_address = Web3.to_checksum_address(silo_address)
            silo_lens_address = Web3.to_checksum_address(cls.SILO_LENS_ADDRESS)

            silo_lens_abi = cls.get_silo_lens_abi()
            silo_abi = cls.get_silo_abi()
            silo_config_abi = cls.get_silo_config_abi()
            irm_config_abi = cls.get_irm_config_abi()
            if not silo_lens_abi or not silo_abi or not silo_config_abi or not irm_config_abi:
                return None

            silo_contract = w3.eth.contract(address=silo_address, abi=silo_abi)
            lens_contract = w3.eth.contract(address=silo_lens_address, abi=silo_lens_abi)

            # 1. Get utilization data
            utilization_data = silo_contract.functions.utilizationData().call()
            collateral_assets = utilization_data[0]
            debt_assets = utilization_data[1]
            interest_rate_timestamp = utilization_data[2]

            # 2. Get symbol and decimals
            try:
                symbol = silo_contract.functions.symbol().call()
            except Exception:
                symbol = "TOKEN"
            try:
                decimals = silo_contract.functions.decimals().call()
            except Exception:
                decimals = 18
            def scale(value):
                return float(value) / (10 ** decimals)
                
            # Calculate values
            total_supplied = scale(collateral_assets)
            total_borrowed = scale(debt_assets)
            
            # Calculate utilization rate
            utilization_rate = total_borrowed / total_supplied if total_supplied > 0 else 0

            # 3. Get config address
            config_address = silo_contract.functions.config().call()
            config_contract = w3.eth.contract(address=config_address, abi=silo_config_abi)

            # 4. Get config struct (contains interest rate model address)
            config_struct = config_contract.functions.getConfig(silo_address).call()
            irm_address = config_struct[9]

            # 5. Get irmConfig address from IRM contract
            irm_contract = w3.eth.contract(address=irm_address, abi=[{"inputs":[],"name":"irmConfig","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}])
            irm_config_address = irm_contract.functions.irmConfig().call()
            irm_config_contract = w3.eth.contract(address=irm_config_address, abi=irm_config_abi)
            irm_params = irm_config_contract.functions.getConfig().call()
            # Unpack IRM params
            uopt, ucrit, ulow, ki, kcrit, klow, klin, beta, ri, Tcrit = irm_params

            # 6. Get reserve_factor as daoFee from lens
            try:
                fees = lens_contract.functions.getFeesAndFeeReceivers(silo_address).call()
                dao_fee = float(fees[2]) / 1e18
            except Exception:
                dao_fee = 0.0

            # 7. Prepare output (only required fields)
            reserve_info = {
                'name': f"{symbol} Reserve {cls.SOURCE}",
                'protocol': cls.PROTOCOL_NAME,
                'network': cls.NETWORK_NAME,
                'type': 'reserve',
                'total_supplied': total_supplied,
                'total_borrowed': total_borrowed,
                'utilization_rate': utilization_rate,
                'interest_rate_timestamp': int(interest_rate_timestamp),
                'reserve_factor': dao_fee,
                'source': cls.SOURCE,

                'irm_params': {
                    'uopt': float(uopt),
                    'ucrit': float(ucrit),
                    'ulow': float(ulow),
                    'ki': float(ki),
                    'kcrit': float(kcrit),
                    'klow': float(klow),
                    'klin': float(klin),
                    'beta': float(beta),
                    'ri': float(ri),
                    'Tcrit': float(Tcrit)
                }
            }
            return reserve_info
        except Exception as e:
            logging.error(f"[SILO] Error processing reserve: {str(e)}")
            return None

    @classmethod
    def calculate_reserve_apy(cls, our_supply, reserve_data):
        """
        Calculate APY for Silo reserve using Silo IRM formula (calculateCurrentInterestRate)
        """
        import time
        # Unpack IRM params and scale to human values
        irm = reserve_data.get('irm_params', {})
        scale = 1e18
        uopt = irm.get('uopt', 0.8 * scale) / scale
        ucrit = irm.get('ucrit', 0.9 * scale) / scale
        ulow = irm.get('ulow', 0.7 * scale) / scale
        ki = irm.get('ki', 0.1 * scale) / scale
        kcrit = irm.get('kcrit', 0.2 * scale) / scale
        klow = irm.get('klow', 0.05 * scale) / scale
        klin = irm.get('klin', 0.08 * scale) / scale
        beta = irm.get('beta', 0.1 * scale) / scale
        ri = irm.get('ri', 0.01 * scale) / scale
        Tcrit = irm.get('Tcrit', 0) / scale
        _DP = 1.0
        # Get current time
        now = int(time.time())
        # Unpack supplied/borrowed
        total_supplied = reserve_data.get('total_supplied', 0) + our_supply
        total_borrowed = reserve_data.get('total_borrowed', 0)
        interest_rate_timestamp = reserve_data.get('interest_rate_timestamp', now)
        reserve_factor = reserve_data.get('reserve_factor', 0.15)
        # Utilization
        u = total_borrowed / total_supplied if total_supplied > 0 else 0
        # T := now - last update
        T = now - interest_rate_timestamp
        DP = _DP
        # --- Silo IRM formula ---
        # rp
        if u > ucrit:
            rp = kcrit * (DP + Tcrit + beta * T) / DP * (u - ucrit) / DP
        else:
            rp = min(0, klow * (u - ulow) / DP)
        # rlin
        rlin = klin * u / DP
        # ri
        ri_val = max(ri, rlin)
        ri_val = max(ri_val + ki * (u - uopt) * T / DP, rlin)
        # rcur
        rcur = max(ri_val + rp, rlin)
        rcur = rcur * 365 * 24 * 3600  # 365 days
        # Cap (10_000% per year)
        rcur = min(rcur, 1e2)  # 1e2 == 100 (10000%) since we are now in human scale
        # Supply APY (minus reserve factor)
        supply_apy = rcur * (total_borrowed / total_supplied) * (1 - reserve_factor) if total_supplied > 0 else 0
        rewards_apy = 0.0
        total_apy = supply_apy + rewards_apy
        
        # Calculate APR from APY using the formula: APR = 365 * [(1 + APY)^(1/365) - 1]
        supply_apr = 365 * ((1 + supply_apy) ** (1/365) - 1) if supply_apy > 0 else 0
        rewards_apr = 365 * ((1 + rewards_apy) ** (1/365) - 1) if rewards_apy > 0 else 0
        total_apr = supply_apr + rewards_apr
        
        return supply_apy, rewards_apy, total_apy, supply_apr, rewards_apr, total_apr 