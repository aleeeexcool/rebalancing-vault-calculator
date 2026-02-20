from scipy.optimize import root_scalar, minimize

class FundParameters:
    def __init__(self, total_borrowed1, total_supplied1, optimal_usage_ratio1, 
                 variable_rate_slope1_1, variable_rate_slope2_1, token_price1,
                 total_borrowed2, total_supplied2, optimal_usage_ratio2,
                 variable_rate_slope1_2, variable_rate_slope2_2, token_price2,
                 fee_percentage, base_variable_borrow_rate=0,
                 rewards_per_year1=0, rewards_per_year2=0):
        self.fund1 = {
            'total_borrowed': total_borrowed1,
            'total_supplied': total_supplied1,
            'optimal_usage_ratio': optimal_usage_ratio1,
            'variable_rate_slope1': variable_rate_slope1_1,
            'variable_rate_slope2': variable_rate_slope2_1,
            'token_price': token_price1,
            'rewards_per_year': rewards_per_year1
        }
        self.fund2 = {
            'total_borrowed': total_borrowed2,
            'total_supplied': total_supplied2,
            'optimal_usage_ratio': optimal_usage_ratio2,
            'variable_rate_slope1': variable_rate_slope1_2,
            'variable_rate_slope2': variable_rate_slope2_2,
            'token_price': token_price2,
            'rewards_per_year': rewards_per_year2
        }
        self.fee_percentage = fee_percentage
        self.base_variable_borrow_rate = base_variable_borrow_rate

def f_pool1(x, params):
    fund = params.fund1
    utilization = (fund['total_borrowed'] / (fund['total_supplied'] + x))

    if utilization <= fund['optimal_usage_ratio']:
        borrowAPY = params.base_variable_borrow_rate + (fund['variable_rate_slope1'] * utilization) / fund['optimal_usage_ratio']
    else:
        excess = (utilization - fund['optimal_usage_ratio']) / (1 - fund['optimal_usage_ratio'])
        borrowAPY = params.base_variable_borrow_rate + (fund['variable_rate_slope1'] * utilization) + (fund['variable_rate_slope2'] * excess)

    total_borrowed_APY_USD = borrowAPY * fund['total_borrowed'] * fund['token_price']
    total_supplied_APY = (total_borrowed_APY_USD * (1 - params.fee_percentage)) / (fund['total_supplied'] + x)
    
    # Add rewards APY if available
    if 'rewards_per_year' in fund and fund['rewards_per_year'] > 0:
        rewards_apr = fund['rewards_per_year'] / ((fund['total_supplied'] + x) * fund['token_price']) / 2
        # Convert rewards APR to APY
        rewards_apy = (1 + rewards_apr/365)**365 - 1
        total_supplied_APY += rewards_apy

    return total_supplied_APY
    
def f_pool2(x, params):
    fund = params.fund2
    utilization = (fund['total_borrowed'] / (fund['total_supplied'] + x))

    if utilization <= fund['optimal_usage_ratio']:
        borrowAPY = params.base_variable_borrow_rate + (fund['variable_rate_slope1'] * utilization) / fund['optimal_usage_ratio']
    else:
        excess = (utilization - fund['optimal_usage_ratio']) / (1 - fund['optimal_usage_ratio'])
        borrowAPY = params.base_variable_borrow_rate + (fund['variable_rate_slope1'] * utilization) + (fund['variable_rate_slope2'] * excess)

    total_borrowed_APY_USD = borrowAPY * fund['total_borrowed'] * fund['token_price']
    total_supplied_APY = (total_borrowed_APY_USD * (1 - params.fee_percentage)) / (fund['total_supplied'] + x)
    
    # Add rewards APY if available
    if 'rewards_per_year' in fund and fund['rewards_per_year'] > 0:
        rewards_apr = fund['rewards_per_year'] / ((fund['total_supplied'] + x) * fund['token_price']) / 2
        # Convert rewards APR to APY
        rewards_apy = (1 + rewards_apr/365)**365 - 1
        total_supplied_APY += rewards_apy

    return total_supplied_APY

def calculate_distribution(total_supply, params):
    def objective(vars):
        x, y = vars
        result = x * f_pool1(x, params) + (y) * f_pool2(y, params)
        return -result


    # Equality constraint: x + y + z = total_supply
    constraints = [
        {'type': 'eq', 'fun': lambda vars: total_supply - sum(vars)}  # should be zero when satisfied
    ]

    # Bounds: x, y, z ≥ 0
    bounds = [(0, total_supply)] * 2

    # Initial guess (equally distributed)
    initial_guess = [total_supply / 2] * 2

    # Run optimizer
    result = minimize(objective, initial_guess, method='trust-constr', bounds=bounds, constraints=constraints)


    # def df_dx(x, h=1e-5):
    #     return (equation(x + h) - equation(x - h)) / (2 * h)

    # solution = root_scalar(df_dx, bracket=[1, total_supply-1], method='brentq')
    
    # x_opt = solution.root
    # y_opt = total_supply - x_opt
    print("result", result)
    if result.success:
        x_opt, y_opt = result.x
        max_val = -result.fun  # flip sign back
        return {
            'reserve1_supply': round(x_opt, 6),
            'reserve2_supply': round(y_opt, 6),
            'total_profit': max_val
        }
    else:
        raise ValueError(f"Optimization failed: {result.message}")
