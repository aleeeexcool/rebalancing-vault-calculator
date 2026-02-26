"""
Shared utilities for all protocol adapters
"""
import logging
import requests
from web3 import Web3
import json
import os
from .cache_manager import cache_manager

def get_token_price(token_address):
    try:
        response = requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{token_address}')
        if response.status_code == 200:
            data = response.json()
            if data.get('pairs') and len(data['pairs']) > 0:
                return float(data['pairs'][0].get('priceUsd', 0))
        return 0
    except Exception as e:
        logging.error(f"Error getting token price: {e}")
        return 0
        
def get_reserve_name(address):
    try:
        # Cached lookup
        cache_key = f"reserve_name_{address.lower()}"
        cached_name = cache_manager.get_cached_data('utils', 'mantle', cache_key)
        if cached_name:
            return cached_name.get('name')
        
        # API call
        response = requests.get(f"https://coins.llama.fi/prices/current/mantle:{address}")
        if response.status_code == 200:
            data = response.json()
            coin_data = data.get('coins', {}).get(f'mantle:{address}')
            if coin_data:
                name = coin_data.get('symbol')
                cache_manager.set_cached_data('utils', 'mantle', cache_key, {
                    'name': name
                }, ttl=86400)
                return name
        
        # Cache failed result
        cache_manager.set_cached_data('utils', 'mantle', cache_key, {
            'name': None
        }, ttl=3600)
        
        return None
    except Exception as e:
        logging.error(f"Error getting reserve name: {e}")
        return None 