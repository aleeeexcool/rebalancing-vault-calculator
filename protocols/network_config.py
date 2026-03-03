"""
Network configuration for different loan assets in Morpho markets
"""

NETWORK_PRESETS = {
    "ethereum": {
        "network": "ethereum",
        "rpc_url": "https://eth.llamarpc.com",
        "irm_contract": "0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC",
        "abi_path": "abi/morphoEthereumInterestRateModelAbi.json"
    },
    "base": {
        "network": "base",
        "rpc_url": "https://base-rpc.publicnode.com",
        "irm_contract": "0x46415998764C29aB2a25CbeA6254146D50D22687",
        "abi_path": "abi/morphoEthereumInterestRateModelAbi.json"
    },
    "hyperevm": {
        "network": "hyperevm",
        "rpc_url": "https://rpc.hyperliquid.xyz/evm",
        "irm_contract": "0xD4a426F010986dCad727e8dd6eed44cA4A9b7483",
        "abi_path": "abi/morphoEthereumInterestRateModelAbi.json"
    }
}

NETWORK_CONFIG = {
    # Ethereum USDC
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": NETWORK_PRESETS["ethereum"],
    # Ethereum USDT
    "0xdAC17F958D2ee523a2206206994597C13D831ec7": NETWORK_PRESETS["ethereum"],
    # Base USDC
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913": NETWORK_PRESETS["base"],
    # HyperEVM USD₮0
    "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb": NETWORK_PRESETS["hyperevm"],
} 