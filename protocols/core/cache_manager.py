"""
Cache Manager for Protocol Adapters
Handles caching of static protocol data to reduce RPC calls
"""
import json
import os
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

class CacheManager:
    """
    Manages caching for protocol adapters
    """
    
    def __init__(self, cache_dir: str = "cache", default_ttl: int = 86400):
        """
        Initialize cache manager
        
        Args:
            cache_dir: Directory to store cache files
            default_ttl: Default time-to-live in seconds (24 hours by default)
        """
        self.cache_dir = cache_dir
        self.default_ttl = default_ttl
        self._ensure_cache_dir()
    
    def _ensure_cache_dir(self):
        """Ensure cache directory exists"""
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
    
    def _get_cache_file_path(self, protocol: str, network: str, address: str) -> str:
        """
        Get cache file path for a specific protocol/network/address combination
        
        Args:
            protocol: Protocol name (e.g., 'lendle')
            network: Network name (e.g., 'mantle')
            address: Contract address
            
        Returns:
            str: Path to cache file
        """
        filename = f"{protocol}_{network}_{address.lower()}.json"
        return os.path.join(self.cache_dir, filename)
    
    def get_cached_data(self, protocol: str, network: str, address: str) -> Optional[Dict[str, Any]]:
        """
        Get cached data for a protocol/network/address combination
        
        Args:
            protocol: Protocol name
            network: Network name
            address: Contract address
            
        Returns:
            dict: Cached data if valid, None if not found or expired
        """
        cache_file = self._get_cache_file_path(protocol, network, address)
        
        if not os.path.exists(cache_file):
            return None
        
        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            # Check if cache is expired
            cached_time = cache_data.get('cached_at', 0)
            ttl = cache_data.get('ttl', self.default_ttl)
            
            if time.time() - cached_time > ttl:
                logging.info(f"Cache expired for {protocol}/{network}/{address}")
                return None
            
            return cache_data.get('data')
            
        except Exception as e:
            logging.error(f"Error reading cache file {cache_file}: {e}")
            return None
    
    def set_cached_data(self, protocol: str, network: str, address: str, data: Dict[str, Any], ttl: Optional[int] = None):
        """
        Cache data for a protocol/network/address combination
        
        Args:
            protocol: Protocol name
            network: Network name
            address: Contract address
            data: Data to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        cache_file = self._get_cache_file_path(protocol, network, address)
        
        cache_data = {
            'cached_at': time.time(),
            'ttl': ttl or self.default_ttl,
            'data': data
        }
        
        try:
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            logging.info(f"Cached data for {protocol}/{network}/{address}")
            
        except Exception as e:
            logging.error(f"Error writing cache file {cache_file}: {e}")
    
    def invalidate_cache(self, protocol: str, network: str, address: str):
        """
        Invalidate cache for a specific protocol/network/address combination
        
        Args:
            protocol: Protocol name
            network: Network name
            address: Contract address
        """
        cache_file = self._get_cache_file_path(protocol, network, address)
        
        if os.path.exists(cache_file):
            try:
                os.remove(cache_file)
                logging.info(f"Invalidated cache for {protocol}/{network}/{address}")
            except Exception as e:
                logging.error(f"Error removing cache file {cache_file}: {e}")
    
    def clear_all_cache(self):
        """Clear all cached data"""
        try:
            for filename in os.listdir(self.cache_dir):
                if filename.endswith('.json'):
                    os.remove(os.path.join(self.cache_dir, filename))
            logging.info("Cleared all cache data")
        except Exception as e:
            logging.error(f"Error clearing cache: {e}")
    
    def clear_protocol_cache(self, protocol: str, network: str = None):
        """
        Clear all cache for a specific protocol, optionally filtered by network
        
        Args:
            protocol: Protocol name (e.g., 'lendle', 'aave')
            network: Network name (e.g., 'mantle', 'base'). If None, clears all networks
        """
        try:
            cleared_count = 0
            for filename in os.listdir(self.cache_dir):
                if filename.endswith('.json'):
                    # Parse filename: protocol_network_address.json
                    parts = filename[:-5].split('_', 2)  # Remove .json and split
                    if len(parts) >= 2:
                        file_protocol = parts[0]
                        file_network = parts[1]
                        
                        # Check if this file matches our criteria
                        if file_protocol.lower() == protocol.lower():
                            if network is None or file_network.lower() == network.lower():
                                os.remove(os.path.join(self.cache_dir, filename))
                                cleared_count += 1
            
            if network:
                logging.info(f"Cleared {cleared_count} cache files for {protocol}/{network}")
            else:
                logging.info(f"Cleared {cleared_count} cache files for {protocol}")
                
        except Exception as e:
            logging.error(f"Error clearing protocol cache: {e}")
    
    def clear_expired_cache(self):
        """Clear all expired cache entries"""
        try:
            cleared_count = 0
            current_time = time.time()
            
            for filename in os.listdir(self.cache_dir):
                if filename.endswith('.json'):
                    cache_file = os.path.join(self.cache_dir, filename)
                    try:
                        with open(cache_file, 'r') as f:
                            cache_data = json.load(f)
                        
                        cached_time = cache_data.get('cached_at', 0)
                        ttl = cache_data.get('ttl', self.default_ttl)
                        
                        if current_time - cached_time > ttl:
                            os.remove(cache_file)
                            cleared_count += 1
                            
                    except Exception as e:
                        logging.warning(f"Error checking cache file {filename}: {e}")
                        # Remove corrupted cache files
                        os.remove(cache_file)
                        cleared_count += 1
            
            logging.info(f"Cleared {cleared_count} expired cache files")
            
        except Exception as e:
            logging.error(f"Error clearing expired cache: {e}")
    
    def get_cache_stats(self):
        """
        Get cache statistics
        
        Returns:
            dict: Cache statistics including total files, size, protocols, etc.
        """
        try:
            stats = {
                'total_files': 0,
                'total_size_bytes': 0,
                'protocols': {},
                'expired_files': 0
            }
            
            current_time = time.time()
            
            for filename in os.listdir(self.cache_dir):
                if filename.endswith('.json'):
                    cache_file = os.path.join(self.cache_dir, filename)
                    file_size = os.path.getsize(cache_file)
                    
                    stats['total_files'] += 1
                    stats['total_size_bytes'] += file_size
                    
                    # Parse protocol from filename
                    parts = filename[:-5].split('_', 2)
                    if len(parts) >= 1:
                        protocol = parts[0]
                        if protocol not in stats['protocols']:
                            stats['protocols'][protocol] = 0
                        stats['protocols'][protocol] += 1
                    
                    # Check if expired
                    try:
                        with open(cache_file, 'r') as f:
                            cache_data = json.load(f)
                        
                        cached_time = cache_data.get('cached_at', 0)
                        ttl = cache_data.get('ttl', self.default_ttl)
                        
                        if current_time - cached_time > ttl:
                            stats['expired_files'] += 1
                            
                    except Exception:
                        stats['expired_files'] += 1
            
            # Convert size to human readable
            if stats['total_size_bytes'] > 1024 * 1024:
                stats['total_size_mb'] = round(stats['total_size_bytes'] / (1024 * 1024), 2)
            elif stats['total_size_bytes'] > 1024:
                stats['total_size_kb'] = round(stats['total_size_bytes'] / 1024, 2)
            
            return stats
            
        except Exception as e:
            logging.error(f"Error getting cache stats: {e}")
            return {}

# Global cache manager instance
cache_manager = CacheManager() 