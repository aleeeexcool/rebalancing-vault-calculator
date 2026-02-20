from scipy.optimize import minimize
import numpy as np
import requests
from protocols.core.protocol_factory import ProtocolFactory

class InvestmentParameters:
    def __init__(self, investments_data, fee_percentage=0, base_variable_borrow_rate=0):
        self.investments = investments_data
        self.fee_percentage = fee_percentage
        self.base_variable_borrow_rate = base_variable_borrow_rate
        self.investment_count = len(investments_data)
        
        # Divide investments into reserves and pools for convenience
        self.reserves = [inv for inv in investments_data if inv['type'] == 'reserve']
        self.pools = [inv for inv in investments_data if inv['type'] == 'pool']

def get_token_price(token_address):
    """
    Get token price from Dexscreener API.
    
    Args:
        token_address: Token contract address
        
    Returns:
        float: Token price in USD, or None if price cannot be fetched
    """
    try:
        response = requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{token_address}')
        if response.status_code == 200:
            data = response.json()
            # Get the first pair from the response as it's usually the most liquid one
            if data.get('pairs') and len(data['pairs']) > 0:
                return float(data['pairs'][0].get('priceUsd', 0))
        return 0
    except Exception as e:
        print(f"Error getting token price: {e}")
        return 0

# Utility functions for backward compatibility
def calculate_init_borrow_rate(utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
    """
    Calculate borrow rate for INIT reserves
    """
    from protocols.init_mantle_adapter import InitAdapter
    return InitAdapter.calculate_borrow_rate(
        utilization, optimal_usage_ratio, base_variable_borrow_rate, 
        variable_rate_slope1, variable_rate_slope2
    )

def calculate_lendle_borrow_rate(utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
    """
    Calculate borrow rate for LENDLE reserves
    """
    from protocols.lendle_mantle_adapter import LendleMantleAdapter
    return LendleMantleAdapter.calculate_borrow_rate(
        utilization, optimal_usage_ratio, base_variable_borrow_rate, 
        variable_rate_slope1, variable_rate_slope2
    )

def calculate_pool_apr(daily_fee, pool_distribution, protocol_fee=0):
    """
    Calculate pool APR
    """
    return (daily_fee * 365 * (1 - protocol_fee)) / pool_distribution

def calculate_rewards_apr(reward_per_day, reward_token_price, pool_distribution):
    """
    Calculate rewards APR
    """
    return (reward_per_day * reward_token_price * 365) / pool_distribution

def calculate_pool_apy(apr):
    """
    Convert APR to APY
    """
    return (1 + apr/365)**365 - 1

def calculate_reserve_apy(our_supply, index, params):
    """
    Calculate APY and APR for a reserve
    
    Args:
        our_supply: Amount to supply
        index: Index of investment in params.investments
        params: Investment parameters
        
    Returns:
        tuple: (reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr)
    """
    reserve = params.investments[index]
    
    # Get the appropriate protocol adapter
    adapter = ProtocolFactory.get_protocol_adapter(reserve)
    
    if not adapter:
        # Default to Lendle if adapter not found
        from protocols.lendle_mantle_adapter import LendleMantleAdapter
        adapter = LendleMantleAdapter
    
    return adapter.calculate_reserve_apy(our_supply, reserve)

def calculate_pool_apr_apy(our_supply, index, params):
    """
    Calculate APR and APY for a pool
    
    Args:
        our_supply: Amount to supply
        index: Index of investment in params.investments
        params: Investment parameters
        
    Returns:
        tuple: (base_apy, rewards_apy, total_apy, base_apr, rewards_apr, total_apr)
    """
    pool = params.investments[index]
    
    # Get the appropriate protocol adapter
    adapter = ProtocolFactory.get_protocol_adapter(pool)
    
    if not adapter:
        # Default to MerchantMoe if adapter not found
        from protocols.merchant_moe_mantle_adapter import MerchantMoeAdapter
        adapter = MerchantMoeAdapter
    
    return adapter.calculate_pool_apr_apy(our_supply, pool)

def calculate_investment_metrics(our_supply, index, params):
    """
    Calculate investment metrics using the appropriate protocol adapter
    
    Args:
        our_supply: Amount to supply
        index: Index of investment in params.investments
        params: Investment parameters
        
    Returns:
        tuple: Investment metrics based on investment type
    """
    investment = params.investments[index]
    
    # Get the appropriate protocol adapter
    adapter = ProtocolFactory.get_protocol_adapter(investment)
    
    if not adapter:
        # Default handling based on investment type
        if investment['type'] == 'reserve':
            from protocols.lendle_mantle_adapter import LendleMantleAdapter
            adapter = LendleMantleAdapter
        else:  # pool
            from protocols.merchant_moe_mantle_adapter import MerchantMoeAdapter
            adapter = MerchantMoeAdapter
    
    if investment['type'] == 'reserve':
        return adapter.calculate_reserve_apy(our_supply, investment)
    elif investment['type'] == 'pool':
        return adapter.calculate_pool_apr_apy(our_supply, investment)
    else:
        raise ValueError(f"Unknown investment type: {investment['type']}")

def calculate_optimal_distribution(total_funds, params, min_allocation_percent=0):
    investment_count = params.investment_count
    
    # Objective function to MAXIMIZE — we negate it for minimize()
    def objective(vars):
        value = 0
        for i in range(investment_count):
            metrics = calculate_investment_metrics(vars[i], i, params)
            if params.investments[i]['type'] == 'reserve':  # Reserve metrics
                reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr = metrics
            else:  # Pool metrics
                base_apy, rewards_apy, total_apy, base_apr, rewards_apr, total_apr = metrics
            value += vars[i] * total_apy
        return -value  # negate for maximization

    # Equality constraint: sum of all variables = total_funds
    constraints = [
        {'type': 'eq', 'fun': lambda vars: total_funds - sum(vars)}
    ]

    # Bounds: all variables ≥ 0 and ≤ total_funds
    bounds = [(0, total_funds)] * investment_count

    # Initial guess — equal split
    initial_guess = [total_funds / investment_count] * investment_count

    # Perform optimization
    result = minimize(objective, initial_guess, method='SLSQP', bounds=bounds, constraints=constraints, 
                     options={'maxiter': 1000, 'maxfun': 15000})

    if result.success:
        # Create result dictionary
        distribution = {}
        
        # Add results of distribution
        for i in range(investment_count):
            investment_type = params.investments[i]['type']
            investment_amount = result.x[i]
            distribution[f'{investment_type}{i+1}_supply'] = investment_amount
        
        # Add total expected profit
        distribution['total_profit'] = -result.fun  # flip sign back
        
        # Add detailed information for each investment
        distribution['details'] = []
        for i in range(investment_count):
            inv = params.investments[i]
            amount = result.x[i]
            metrics = calculate_investment_metrics(amount, i, params)
            
            if inv['type'] == 'reserve':
                reserve_apy, rewards_apy, total_apy, reserve_apr, rewards_apr, total_apr = metrics
                profit = amount * total_apy
                # Calculate utilization rate for this allocation
                total_supplied = float(inv.get('total_supplied', 0))
                total_borrowed = float(inv.get('total_borrowed', 0))
                total_supply_with_ours = total_supplied + amount
                utilization = total_borrowed / total_supply_with_ours if total_supply_with_ours > 0 else 0
                
                detail = {
                    'type': inv['type'],
                    'name': inv.get('name', f'{inv["type"]}{i+1}'),
                    'allocated_amount': amount,
                    'percentage': (amount / total_funds) * 100,
                    'total_apy': total_apy * 100,  # renamed from expected_apy
                    'total_apr': total_apr * 100,
                    'expected_profit': profit,
                    'base_apy': reserve_apy * 100,  # renamed from expected_reserve_apy
                    'base_apr': reserve_apr * 100,
                    'rewards_apy': rewards_apy * 100,  # always include rewards_apy
                    'rewards_apr': rewards_apr * 100,
                    'utilization_rate': utilization * 100
                }
            else:  # Pool type
                base_apy, rewards_apy, total_apy, base_apr, rewards_apr, total_apr = metrics
                profit = amount * total_apy
                
                detail = {
                    'type': inv['type'],
                    'name': inv.get('name', f'{inv["type"]}{i+1}'),
                    'allocated_amount': amount,
                    'percentage': (amount / total_funds) * 100,
                    'total_apr': total_apr * 100,
                    'total_apy': total_apy * 100,  # renamed from expected_apy
                    'base_apr': base_apr * 100,
                    'base_apy': base_apy * 100,
                    'rewards_apr': rewards_apr * 100,
                    'rewards_apy': rewards_apy * 100,  # renamed from expected_rewards_apy
                    'expected_profit': profit,
                }
            
            distribution['details'].append(detail)
        
        return distribution
    else:
        raise ValueError(f"Optimization failed: {result.message}")

def create_investment_parameters(investments_data, fee_percentage=0, base_rate=0):
        
    return InvestmentParameters(investments_data, fee_percentage, base_rate)
