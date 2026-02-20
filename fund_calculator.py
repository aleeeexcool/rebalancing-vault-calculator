import logging
import numpy as np
import matplotlib.pyplot as plt
import requests
from flask import Flask, jsonify, send_file, request
from flask_cors import CORS
import io
from calculations.scalar_calculator_2funds import FundParameters as FundParameters2, calculate_distribution as calculate_distribution_2, f_pool1 as f_pool1_2, f_pool2 as f_pool2_2
from calculations.scalar_calculator_3funds import FundParameters as FundParameters3, calculate_distribution as calculate_distribution_3, f_pool1 as f_pool1_3, f_pool2 as f_pool2_3, f_pool3 as f_pool3_3
from calculations.scalar_calculator_4funds import FundParameters as FundParameters4, calculate_distribution as calculate_distribution_4, f_pool1 as f_pool1_4, f_pool2 as f_pool2_4, f_pool3 as f_pool3_4, f_pool4 as f_pool4_4
from universal_pool_reserve_calculator import create_investment_parameters, calculate_optimal_distribution, calculate_reserve_apy
from scipy.optimize import minimize
from protocols.core.protocol_factory import ProtocolFactory
import asyncio
from protocols.morpho_markets import get_morpho_markets_data, UnsupportedLoanAssetError
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('fund_calculator.log'),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

def validate_parameters(params):
    """Validate and convert input parameters for any number of funds"""
    # Base parameters that are always required
    base_params = {
        'fee_percentage': float,
        'total_my_supply': int
    }
    
    # Detect number of funds by looking for fund-specific parameters
    num_funds = 0
    while True:
        fund_num = num_funds + 1
        fund_params = [
            f'total_supplied{fund_num}',
            f'total_borrowed{fund_num}',
            f'optimal_usage_ratio{fund_num}',
            f'variable_rate_slope1_{fund_num}',
            f'variable_rate_slope2_{fund_num}',
            f'token_price{fund_num}'
        ]
        if all(param in params for param in fund_params):
            num_funds += 1
        else:
            break
    
    if num_funds < 2:
        raise ValueError("At least 2 funds are required")
    
    # Build the complete required parameters dictionary
    required_params = base_params.copy()
    for fund_num in range(1, num_funds + 1):
        fund_params = {
            f'total_supplied{fund_num}': int,
            f'total_borrowed{fund_num}': int,
            f'optimal_usage_ratio{fund_num}': float,
            f'variable_rate_slope1_{fund_num}': float,
            f'variable_rate_slope2_{fund_num}': float,
            f'token_price{fund_num}': float
        }
        required_params.update(fund_params)
    
    # Optional parameters for each fund
    for fund_num in range(1, num_funds + 1):
        # Add rewards_per_year parameter as optional
        if f'rewards_per_year{fund_num}' in params:
            try:
                value = params[f'rewards_per_year{fund_num}']
                params[f'rewards_per_year{fund_num}'] = value if isinstance(value, float) else float(value)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid value for rewards_per_year{fund_num}: must be float")
    
    # Validate and convert parameters
    result = {'num_funds': num_funds}
    for param_name, param_type in required_params.items():
        if param_name not in params:
            raise ValueError(f"Missing required parameter: {param_name}")
        try:
            value = params[param_name]
            result[param_name] = value if isinstance(value, param_type) else param_type(value)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid value for {param_name}: must be {param_type.__name__}")
    
    # Additional validation for total_my_supply
    if result['total_my_supply'] < 1:
        raise ValueError("Total supply must be at least 1.")
    # Add rewards_per_year parameter as optional
    for fund_num in range(1, num_funds + 1):
            if f'rewards_per_year{fund_num}' in params:
                result[f'rewards_per_year{fund_num}'] = params[f'rewards_per_year{fund_num}']
    
    # Copy optional rewards parameters if they exist
    for fund_num in range(1, num_funds + 1):
        key = f'rewards_per_year{fund_num}'
        if key in params: # Check original params
            try:
                value = params[key]
                # Ensure it's a float, convert if necessary
                result[key] = float(value) 
            except (ValueError, TypeError):
                 raise ValueError(f"Invalid value for {key}: must be a number")

    return result

def generate_interest_rate_plot_2spots(params, max_values):
    """
    Generate interest rate model plot for both funds
    
    Args:
        params: Dictionary containing all input parameters
        max_values: Dictionary containing calculated maximum profit values
    
    Returns:
        BytesIO object containing the generated PNG image
    """
    # Create x values (utilization from 0 to 100%)
    x = np.linspace(0, 100, 1000)

    # Create figure with two subplots vertically stacked
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    # Calculate current utilization rates
    current_utilization1 = (params['total_borrowed1']/params['total_supplied1'] * 100)
    current_utilization2 = (params['total_borrowed2']/params['total_supplied2'] * 100)

    # First subplot (Fund 1)
    y1 = np.zeros_like(x)
    optimal_point1 = params['optimal_usage_ratio1'] * 100

    for i, utilization in enumerate(x):
        y1[i] = calculate_theoretical_supplied_apy(
            i / 1000, 0, params['total_supplied1'], params['total_borrowed1'], params['optimal_usage_ratio1'], 
            0, params['variable_rate_slope1_1'], params['variable_rate_slope2_1'],
            params['fee_percentage'], params['token_price1']) 

    ax1.plot(x, y1 * 100, 'b-', linewidth=2)
    ax1.plot([optimal_point1, optimal_point1], [0, 50], color='r', linestyle='--', alpha=0.5)
    ax1.plot([current_utilization1, current_utilization1], [0, 50], color='g', linestyle='--', alpha=0.5)
    
    current_utilization_APY1 = calculate_theoretical_supplied_apy(
        current_utilization1 / 100, 0, params['total_supplied1'], params['total_borrowed1'], 
        params['optimal_usage_ratio1'], 0, params['variable_rate_slope1_1'], 
        params['variable_rate_slope2_1'], params['fee_percentage'], params['token_price1']) 
    
    ax1.plot(current_utilization1, current_utilization_APY1 * 100, 'go', markersize=8)

    maxUtilization1 = max_values['maxUtilization1']
    max_profit_supply1 = max_values['max_profit_supply1']
    max_profit_APY1 = max_values['max_profit_APY1']

    ax1.plot([maxUtilization1, maxUtilization1], [0, 70], color='purple', linestyle='--', alpha=0.5)
    ax1.plot(maxUtilization1, max_profit_APY1 * 100, 'go', markersize=8)
    
    optimalPointAPY1 = calculate_theoretical_supplied_apy(
        optimal_point1 / 100, 0, params['total_supplied1'], params['total_borrowed1'], 
        params['optimal_usage_ratio1'], 0, params['variable_rate_slope1_1'], 
        params['variable_rate_slope2_1'], params['fee_percentage'], params['token_price1']) 
    
    ax1.plot(optimal_point1, optimalPointAPY1 * 100, 'go', markersize=8)

    ax1.text(optimal_point1, 55, f'Optimal {optimal_point1}% \n Supply APY: {optimalPointAPY1 * 100 :.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8)
    ax1.text(current_utilization1, 55, 
             f'Utilization Rate: {current_utilization1:.1f}% \n Supply APY: {current_utilization_APY1 * 100 :.2f}% \n Current Borrowed: {params["total_borrowed1"]:.0f} \n Current Supply: {params["total_supplied1"]:.0f}', 
             rotation=0, verticalalignment='top', fontsize=8)
    ax1.text(maxUtilization1, 80, f'Maximum Utilization Rate: {maxUtilization1:.1f}%\nFund 1 Supply: {max_profit_supply1:.0f}, \nSupply APY: {max_profit_APY1 * 100:.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8)

    # Second subplot (Fund 2)
    y2 = np.zeros_like(x)
    optimal_point2 = params['optimal_usage_ratio2'] * 100

    for i, utilization in enumerate(x):
        y2[i] = calculate_theoretical_supplied_apy(
            i / 1000, 0, params['total_supplied2'], params['total_borrowed2'], 
            params['optimal_usage_ratio2'], 0, params['variable_rate_slope1_2'], 
            params['variable_rate_slope2_2'], params['fee_percentage'], params['token_price2']) 

    ax2.plot(x, y2 * 100, 'b-', linewidth=2)
    ax2.plot([optimal_point2, optimal_point2], [0, 50], color='r', linestyle='--', alpha=0.5)
    ax2.plot([current_utilization2, current_utilization2], [0, 50], color='g', linestyle='--', alpha=0.5)
    
    current_utilization_APY2 = calculate_theoretical_supplied_apy(
        current_utilization2 / 100, 0, params['total_supplied2'], params['total_borrowed2'], 
        params['optimal_usage_ratio2'], 0, params['variable_rate_slope1_2'], 
        params['variable_rate_slope2_2'], params['fee_percentage'], params['token_price1']) 
    
    ax2.plot(current_utilization2, current_utilization_APY2 * 100, 'go', markersize=8)

    maxUtilization2 = max_values['maxUtilization2']
    max_profit_supply2 = max_values['max_profit_supply2']
    max_profit_APY2 = max_values['max_profit_APY2']

    ax2.plot([maxUtilization2, maxUtilization2], [0, 70], color='purple', linestyle='--', alpha=0.5)
    ax2.plot(maxUtilization2, max_profit_APY2 * 100, 'go', markersize=8)
    
    optimalPointAPY2 = calculate_theoretical_supplied_apy(
        optimal_point2 / 100, 0, params['total_supplied2'], params['total_borrowed2'], 
        params['optimal_usage_ratio2'], 0, params['variable_rate_slope1_2'], 
        params['variable_rate_slope2_2'], params['fee_percentage'], params['token_price2']) 
    
    ax2.plot(optimal_point2, optimalPointAPY2 * 100, 'go', markersize=8)

    ax2.text(optimal_point2, 55, f'Optimal {optimal_point2}% \n Supply APY: {optimalPointAPY2 * 100 :.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8) 
    ax2.text(current_utilization2, 55, 
             f'Utilization Rate: {current_utilization2:.1f}% \n Supply APY: {current_utilization_APY2 * 100 :.2f}% \n Current Borrowed: {params["total_borrowed2"]:.0f} \n Current Supply: {params["total_supplied2"]:.0f}', 
             rotation=0, verticalalignment='top', fontsize=8)
    ax2.text(maxUtilization2, 80, f'Maximum Utilization Rate: {maxUtilization2:.1f}%\nFund 2 Supply: {max_profit_supply2:.0f}, \nSupply APY: {max_profit_APY2 * 100:.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8)

    # Configure both subplots
    for ax in [ax1, ax2]:
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        ax.set_xticks(np.arange(5, 101, 25))
        ax.set_yticks(np.arange(5, 101, 50))
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.set_xlabel('Utilization Rate (%)')
        ax.set_ylabel('Interest Rate (%)')

    # Set titles
    ax1.set_title('Fund 1 Interest Rate Model')
    ax2.set_title('Fund 2 Interest Rate Model')

    # Adjust layout
    plt.tight_layout()

    # Save to BytesIO instead of file
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()

    return img_bytes

def generate_interest_rate_plot_3spots(params, max_values):
    """
    Generate interest rate model plot for both funds
    
    Args:
        params: Dictionary containing all input parameters
        max_values: Dictionary containing calculated maximum profit values
    
    Returns:
        BytesIO object containing the generated PNG image
    """
    # Create x values (utilization from 0 to 100%)
    x = np.linspace(0, 100, 1000)

    # Create figure with trhee subplots vertically stacked
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 12))

    # Calculate current utilization rates
    current_utilization1 = (params['total_borrowed1']/params['total_supplied1'] * 100)
    current_utilization2 = (params['total_borrowed2']/params['total_supplied2'] * 100)
    current_utilization3 = (params['total_borrowed3']/params['total_supplied3'] * 100)
    
    # First subplot (Fund 1)
    y1 = np.zeros_like(x)
    optimal_point1 = params['optimal_usage_ratio1'] * 100

    for i, utilization in enumerate(x):
        y1[i] = calculate_theoretical_supplied_apy(
            i / 1000, 0, params['total_supplied1'], params['total_borrowed1'], params['optimal_usage_ratio1'], 
            0, params['variable_rate_slope1_1'], params['variable_rate_slope2_1'],
            params['fee_percentage'], params['token_price1']) 

    ax1.plot(x, y1 * 100, 'b-', linewidth=2)
    ax1.plot([optimal_point1, optimal_point1], [0, 50], color='r', linestyle='--', alpha=0.5)
    ax1.plot([current_utilization1, current_utilization1], [0, 50], color='g', linestyle='--', alpha=0.5)
    
    current_utilization_APY1 = calculate_theoretical_supplied_apy(
        current_utilization1 / 100, 0, params['total_supplied1'], params['total_borrowed1'], 
        params['optimal_usage_ratio1'], 0, params['variable_rate_slope1_1'], 
        params['variable_rate_slope2_1'], params['fee_percentage'], params['token_price1']) 
    
    ax1.plot(current_utilization1, current_utilization_APY1 * 100, 'go', markersize=8)

    maxUtilization1 = max_values['maxUtilization1']
    max_profit_supply1 = max_values['max_profit_supply1']
    max_profit_APY1 = max_values['max_profit_APY1']

    ax1.plot([maxUtilization1, maxUtilization1], [0, 70], color='purple', linestyle='--', alpha=0.5)
    ax1.plot(maxUtilization1, max_profit_APY1 * 100, 'go', markersize=8)
    
    optimalPointAPY1 = calculate_theoretical_supplied_apy(
        optimal_point1 / 100, 0, params['total_supplied1'], params['total_borrowed1'], 
        params['optimal_usage_ratio1'], 0, params['variable_rate_slope1_1'], 
        params['variable_rate_slope2_1'], params['fee_percentage'], params['token_price1']) 
    
    ax1.plot(optimal_point1, optimalPointAPY1 * 100, 'go', markersize=8)

    ax1.text(optimal_point1, 55, f'Optimal {optimal_point1}% \n Supply APY: {optimalPointAPY1 * 100 :.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8)
    ax1.text(current_utilization1, 55, 
             f'Utilization Rate: {current_utilization1:.1f}% \n Supply APY: {current_utilization_APY1 * 100 :.2f}% \n Current Borrowed: {params["total_borrowed1"]:.0f} \n Current Supply: {params["total_supplied1"]:.0f}', 
             rotation=0, verticalalignment='top', fontsize=8)
    ax1.text(maxUtilization1, 80, f'Maximum Utilization Rate: {maxUtilization1:.1f}%\nFund 1 Supply: {max_profit_supply1:.0f}, \nSupply APY: {max_profit_APY1 * 100:.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8)

    # Second subplot (Fund 2)
    y2 = np.zeros_like(x)
    optimal_point2 = params['optimal_usage_ratio2'] * 100

    for i, utilization in enumerate(x):
        y2[i] = calculate_theoretical_supplied_apy(
            i / 1000, 0, params['total_supplied2'], params['total_borrowed2'], 
            params['optimal_usage_ratio2'], 0, params['variable_rate_slope1_2'], 
            params['variable_rate_slope2_2'], params['fee_percentage'], params['token_price2']) 

    ax2.plot(x, y2 * 100, 'b-', linewidth=2)
    ax2.plot([optimal_point2, optimal_point2], [0, 50], color='r', linestyle='--', alpha=0.5)
    ax2.plot([current_utilization2, current_utilization2], [0, 50], color='g', linestyle='--', alpha=0.5)
    
    current_utilization_APY2 = calculate_theoretical_supplied_apy(
        current_utilization2 / 100, 0, params['total_supplied2'], params['total_borrowed2'], 
        params['optimal_usage_ratio2'], 0, params['variable_rate_slope1_2'], 
        params['variable_rate_slope2_2'], params['fee_percentage'], params['token_price1']) 
    
    ax2.plot(current_utilization2, current_utilization_APY2 * 100, 'go', markersize=8)

    maxUtilization2 = max_values['maxUtilization2']
    max_profit_supply2 = max_values['max_profit_supply2']
    max_profit_APY2 = max_values['max_profit_APY2']

    ax2.plot([maxUtilization2, maxUtilization2], [0, 70], color='purple', linestyle='--', alpha=0.5)
    ax2.plot(maxUtilization2, max_profit_APY2 * 100, 'go', markersize=8)
    
    optimalPointAPY2 = calculate_theoretical_supplied_apy(
        optimal_point2 / 100, 0, params['total_supplied2'], params['total_borrowed2'], 
        params['optimal_usage_ratio2'], 0, params['variable_rate_slope1_2'], 
        params['variable_rate_slope2_2'], params['fee_percentage'], params['token_price2']) 
    
    ax2.plot(optimal_point2, optimalPointAPY2 * 100, 'go', markersize=8)

    ax2.text(optimal_point2, 55, f'Optimal {optimal_point2}% \n Supply APY: {optimalPointAPY2 * 100 :.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8) 
    ax2.text(current_utilization2, 55, 
             f'Utilization Rate: {current_utilization2:.1f}% \n Supply APY: {current_utilization_APY2 * 100 :.2f}% \n Current Borrowed: {params["total_borrowed2"]:.0f} \n Current Supply: {params["total_supplied2"]:.0f}', 
             rotation=0, verticalalignment='top', fontsize=8)
    ax2.text(maxUtilization2, 80, f'Maximum Utilization Rate: {maxUtilization2:.1f}%\nFund 2 Supply: {max_profit_supply2:.0f}, \nSupply APY: {max_profit_APY2 * 100:.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8)

    # Third subplot (Fund 3)
    y3 = np.zeros_like(x)
    optimal_point3 = params['optimal_usage_ratio3'] * 100

    for i, utilization in enumerate(x):
        y3[i] = calculate_theoretical_supplied_apy(
            i / 1000, 0, params['total_supplied3'], params['total_borrowed3'], 
            params['optimal_usage_ratio3'], 0, params['variable_rate_slope1_3'], 
            params['variable_rate_slope2_3'], params['fee_percentage'], params['token_price3']) 

    ax3.plot(x, y3 * 100, 'b-', linewidth=2)
    ax3.plot([optimal_point3, optimal_point3], [0, 50], color='r', linestyle='--', alpha=0.5)
    ax3.plot([current_utilization3, current_utilization3], [0, 50], color='g', linestyle='--', alpha=0.5)
    
    current_utilization_APY3 = calculate_theoretical_supplied_apy(
        current_utilization3 / 100, 0, params['total_supplied3'], params['total_borrowed3'], 
        params['optimal_usage_ratio3'], 0, params['variable_rate_slope1_3'], 
        params['variable_rate_slope2_3'], params['fee_percentage'], params['token_price1']) 
    
    ax3.plot(current_utilization3, current_utilization_APY3 * 100, 'go', markersize=8)

    maxUtilization3 = max_values['maxUtilization3']
    max_profit_supply3 = max_values['max_profit_supply3']
    max_profit_APY3 = max_values['max_profit_APY3']

    ax3.plot([maxUtilization3, maxUtilization3], [0, 70], color='purple', linestyle='--', alpha=0.5)
    ax3.plot(maxUtilization3, max_profit_APY3 * 100, 'go', markersize=8)
    
    optimalPointAPY3 = calculate_theoretical_supplied_apy(
        optimal_point3 / 100, 0, params['total_supplied3'], params['total_borrowed3'], 
        params['optimal_usage_ratio3'], 0, params['variable_rate_slope1_3'], 
        params['variable_rate_slope2_3'], params['fee_percentage'], params['token_price3']) 
    
    ax3.plot(optimal_point3, optimalPointAPY3 * 100, 'go', markersize=8)

    ax3.text(optimal_point3, 55, f'Optimal {optimal_point3}% \n Supply APY: {optimalPointAPY3 * 100 :.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8) 
    ax3.text(current_utilization3, 55, 
             f'Utilization Rate: {current_utilization3:.1f}% \n Supply APY: {current_utilization_APY3 * 100 :.2f}% \n Current Borrowed: {params["total_borrowed3"]:.0f} \n Current Supply: {params["total_supplied3"]:.0f}', 
             rotation=0, verticalalignment='top', fontsize=8)
    ax3.text(maxUtilization3, 80, f'Maximum Utilization Rate: {maxUtilization3:.1f}%\nFund 3 Supply: {max_profit_supply3:.0f}, \nSupply APY: {max_profit_APY3 * 100:.2f}%', 
             rotation=0, verticalalignment='top', fontsize=8)         

    # Configure all subplots
    for ax in [ax1, ax2, ax3]:
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        ax.set_xticks(np.arange(5, 101, 25))
        ax.set_yticks(np.arange(5, 101, 50))
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.set_xlabel('Utilization Rate (%)')
        ax.set_ylabel('Interest Rate (%)')

    # Set titles
    ax1.set_title('Fund 1 Interest Rate Model')
    ax2.set_title('Fund 2 Interest Rate Model')
    ax3.set_title('Fund 3 Interest Rate Model')
    # Adjust layout
    plt.tight_layout()

    # Save to BytesIO instead of file
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()

    return img_bytes

def generate_interest_rate_plot(total_supply, params):
    """
    Generate interest rate model plot for any number of funds
    
    Args:
        total_supply: Total supply to distribute
        params: FundParameters object containing fund parameters (2, 3, or 4 funds)
    
    Returns:
        BytesIO object containing the generated PNG image
    """
    # Create x values (utilization from 0 to 100%)
    x = np.linspace(0, 100, 1000)

    # Determine number of funds
    if isinstance(params, FundParameters2):
        num_funds = 2
    elif isinstance(params, FundParameters3):
        num_funds = 3
    elif isinstance(params, FundParameters4):
        num_funds = 4
    else:
        raise ValueError("Invalid FundParameters type")
    
    # Create figure with subplots
    fig, axes = plt.subplots(num_funds, 1, figsize=(12, 4 * num_funds))
    if num_funds == 1:
        axes = [axes]  # Make it a list for consistency
    
    # Calculate and plot for each fund
    for i in range(num_funds):
        fund_num = i + 1
        ax = axes[i]
        
        # Get fund parameters
        fund = getattr(params, f'fund{fund_num}')
        
        # Calculate current utilization
        current_utilization = (fund['total_borrowed'] / fund['total_supplied']) * 100
        
        # Calculate APY for each utilization point
        y = np.zeros_like(x)
        for j, utilization in enumerate(x):
            if utilization <= fund['optimal_usage_ratio'] * 100:
                borrowAPY = params.base_variable_borrow_rate + (fund['variable_rate_slope1'] * utilization / 100) / fund['optimal_usage_ratio']
            else:
                excess = (utilization / 100 - fund['optimal_usage_ratio']) / (1 - fund['optimal_usage_ratio'])
                borrowAPY = params.base_variable_borrow_rate + (fund['variable_rate_slope1'] * utilization / 100) + (fund['variable_rate_slope2'] * excess)
            
            total_borrowed_APY_USD = borrowAPY * fund['total_borrowed'] * fund['token_price']
            y[j] = (total_borrowed_APY_USD * (1 - params.fee_percentage)) / fund['total_supplied']
        
        # Plot the curve
        ax.plot(x, y * 100, 'b-', linewidth=2)
        
        # Add optimal point line
        optimal_point = fund['optimal_usage_ratio'] * 100
        ax.plot([optimal_point, optimal_point], [0, 50], color='r', linestyle='--', alpha=0.5)
        
        # Add current utilization line
        ax.plot([current_utilization, current_utilization], [0, 50], color='g', linestyle='--', alpha=0.5)
        
        # Calculate and plot current utilization APY
        current_utilization_APY = y[int(current_utilization * 10)]  # Convert to index
        ax.plot(current_utilization, current_utilization_APY * 100, 'go', markersize=8)
        
        # Calculate and plot optimal point APY
        optimal_point_APY = y[int(optimal_point * 10)]  # Convert to index
        ax.plot(optimal_point, optimal_point_APY * 100, 'ro', markersize=8)
        
        # Add annotations
        ax.text(optimal_point, 55, 
                f'Optimal {optimal_point:.1f}% \n Supply APY: {optimal_point_APY * 100:.2f}%', 
                rotation=0, verticalalignment='top', fontsize=8)
        
        ax.text(current_utilization, 55, 
                f'Utilization Rate: {current_utilization:.1f}% \n Supply APY: {current_utilization_APY * 100:.2f}% \n Current Borrowed: {fund["total_borrowed"]:.0f} \n Current Supply: {fund["total_supplied"]:.0f}', 
             rotation=0, verticalalignment='top', fontsize=8)

        # Configure subplot
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        ax.set_xticks(np.arange(5, 101, 25))
        ax.set_yticks(np.arange(5, 101, 50))
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.set_xlabel('Utilization Rate (%)')
        ax.set_ylabel('Interest Rate (%)')
        ax.set_title(f'Fund {fund_num} Interest Rate Model')

    # Adjust layout
    plt.tight_layout()

    # Save to BytesIO
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()

    return img_bytes

@app.route('/api/calc-borrow-supply-apy', methods=['GET'])
def get_calc_borrow_supply_apy():
    """API endpoint that returns the interest rate model plot"""
    try:
        # Get parameters from either JSON body or query string
        if request.is_json:
            params = request.get_json()
        else:
            # Convert query parameters to dict and handle numeric values
            params = {
                    'supply': int(request.args.get('supply')),
                    'debt': int(request.args.get('debt')),
                    'slope1': float(request.args.get('slope1')),
                    'slope2': float(request.args.get('slope2')),
                    'urate': float(request.args.get('urate'))
                    }
        if not params:
            return jsonify({'error': 'No parameters provided'}), 400

        utilization = calculate_theoretical_utilization_rate(params['supply'], 0, params['debt'])

        borrow_APY = calculate_borrow_APY(utilization, params['urate'], 0, params['slope1'], params['slope2'])

        supplied_APY = calculate_theoretical_supplied_apy(utilization, 0, params['supply'], params['debt'], params['urate'], 
                                     0, params['slope1'], params['slope2'],
                                     0.08 , 1)

        return jsonify({'borrow_APY': borrow_APY, 'supplied_APY': supplied_APY})
    
    except Exception as e:
        return jsonify({'error': f'Plot generation error: {str(e)}'}), 500

        
        

@app.route('/api/interest-rate-model', methods=['GET'])
def get_interest_rate_model():
    """API endpoint that returns the interest rate model plot"""
    try:
        # Get parameters from either JSON body or query string
        if request.is_json:
            params = request.get_json()
        else:
            # Convert query parameters to dict and handle numeric values
            params = {}
            for key, value in request.args.items():
                try:
                    float_val = float(value)
                    params[key] = int(float_val) if float_val.is_integer() else float_val
                except ValueError:
                    params[key] = value

        if not params:
            return jsonify({'error': 'No parameters provided'}), 400
        
        # Validate and convert parameters
        try:
            validated_params = validate_parameters(params)
            num_funds = validated_params.pop('num_funds')
            total_supply = validated_params.pop('total_my_supply')
            fee_percentage = validated_params.pop('fee_percentage')
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        
        # Create appropriate fund parameters object and generate plot
        try:
            if num_funds == 2:
                # Use validated parameters from the request
                params = validated_params.copy()
                params['total_my_supply'] = total_supply
                params['fee_percentage'] = fee_percentage
                
                # Calculate max profits
                max_values = calculate_max_profits(params)
                
                # Generate the plot
                img_bytes = generate_interest_rate_plot_2spots(params, max_values)
            elif num_funds == 3:
                params = FundParameters3(
                    total_borrowed1=validated_params['total_borrowed1'],
                    total_supplied1=validated_params['total_supplied1'],
                    optimal_usage_ratio1=validated_params['optimal_usage_ratio1'],
                    variable_rate_slope1_1=validated_params['variable_rate_slope1_1'],
                    variable_rate_slope2_1=validated_params['variable_rate_slope2_1'],
                    token_price1=validated_params['token_price1'],
                    total_borrowed2=validated_params['total_borrowed2'],
                    total_supplied2=validated_params['total_supplied2'],
                    optimal_usage_ratio2=validated_params['optimal_usage_ratio2'],
                    variable_rate_slope1_2=validated_params['variable_rate_slope1_2'],
                    variable_rate_slope2_2=validated_params['variable_rate_slope2_2'],
                    token_price2=validated_params['token_price2'],
                    total_borrowed3=validated_params['total_borrowed3'],
                    total_supplied3=validated_params['total_supplied3'],
                    optimal_usage_ratio3=validated_params['optimal_usage_ratio3'],
                    variable_rate_slope1_3=validated_params['variable_rate_slope1_3'],
                    variable_rate_slope2_3=validated_params['variable_rate_slope2_3'],
                    token_price3=validated_params['token_price3'],
                    fee_percentage=fee_percentage
                )
                params = {
                    'fee_percentage': float(request.args.get('fee_percentage', '0.08')),
                    'total_my_supply': int(request.args.get('total_my_supply', '100000')),
                    'total_supplied1': int(request.args.get('total_supplied1', '370406')),
                    'total_borrowed1': int(request.args.get('total_borrowed1', '179954')),
                    'optimal_usage_ratio1': float(request.args.get('optimal_usage_ratio1', '0.85')),
                    'variable_rate_slope1_1': float(request.args.get('variable_rate_slope1_1', '0.08')),
                    'variable_rate_slope2_1': float(request.args.get('variable_rate_slope2_1', '0.8')),
                    'token_price1': float(request.args.get('token_price1', '1.0')),
                    'total_supplied2': int(request.args.get('total_supplied2', '1792518')),
                    'total_borrowed2': int(request.args.get('total_borrowed2', '522821')),
                    'optimal_usage_ratio2': float(request.args.get('optimal_usage_ratio2', '0.75')),
                    'variable_rate_slope1_2': float(request.args.get('variable_rate_slope1_2', '0.11')),
                    'variable_rate_slope2_2': float(request.args.get('variable_rate_slope2_2', '0.7')),
                    'token_price2': float(request.args.get('token_price2', '1.0')),
                    'total_borrowed3':int(request.args.get('total_borrowed3','179954')),
                    'total_supplied3':int(request.args.get('total_supplied3','370406')),
                    'optimal_usage_ratio3':float(request.args.get('optimal_usage_ratio3','0.85')),
                    'variable_rate_slope1_3':float(request.args.get('variable_rate_slope1_3','0.08')),
                    'variable_rate_slope2_3':float(request.args.get('variable_rate_slope2_3','0.8')),
                    'token_price3':float(request.args.get('token_price3','1.0')),
                }
                # Calculate max profits
                max_values = calculate_max_profits(params)
                
                # Generate the plot
                img_bytes = generate_interest_rate_plot_3spots(params, max_values)
            elif num_funds == 4:
                fund_params = FundParameters4(
                    total_borrowed1=validated_params['total_borrowed1'],
                    total_supplied1=validated_params['total_supplied1'],
                    optimal_usage_ratio1=validated_params['optimal_usage_ratio1'],
                    variable_rate_slope1_1=validated_params['variable_rate_slope1_1'],
                    variable_rate_slope2_1=validated_params['variable_rate_slope2_1'],
                    token_price1=validated_params['token_price1'],
                    total_borrowed2=validated_params['total_borrowed2'],
                    total_supplied2=validated_params['total_supplied2'],
                    optimal_usage_ratio2=validated_params['optimal_usage_ratio2'],
                    variable_rate_slope1_2=validated_params['variable_rate_slope1_2'],
                    variable_rate_slope2_2=validated_params['variable_rate_slope2_2'],
                    token_price2=validated_params['token_price2'],
                    total_borrowed3=validated_params['total_borrowed3'],
                    total_supplied3=validated_params['total_supplied3'],
                    optimal_usage_ratio3=validated_params['optimal_usage_ratio3'],
                    variable_rate_slope1_3=validated_params['variable_rate_slope1_3'],
                    variable_rate_slope2_3=validated_params['variable_rate_slope2_3'],
                    token_price3=validated_params['token_price3'],
                    total_borrowed4=validated_params['total_borrowed4'],
                    total_supplied4=validated_params['total_supplied4'],
                    optimal_usage_ratio4=validated_params['optimal_usage_ratio4'],
                    variable_rate_slope1_4=validated_params['variable_rate_slope1_4'],
                    variable_rate_slope2_4=validated_params['variable_rate_slope2_4'],
                    token_price4=validated_params['token_price4'],
                    fee_percentage=fee_percentage
                )
                img_bytes = generate_interest_rate_plot(total_supply, fund_params)
            else:
                return jsonify({'error': f'Interest rate model for {num_funds} funds is not supported yet'}), 400
                
            
        except Exception as e:
            return jsonify({'error': f'Plot generation error: {str(e)}'}), 500
        
        return send_file(
            img_bytes,
            mimetype='image/png',
            as_attachment=False,
            download_name='interest_rate_model.png'
        )
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def plot_profit_distribution(supply_APY_map1, supply_APY_map2, combined_APY, total_my_supply, max_values):
    """
    Create chart showing total profit distribution
    """
    supplies = list(supply_APY_map1.keys())
    
    # Calculate total profit for each distribution point
    profits = []
    for supply1 in supplies:
        supply2 = total_my_supply - supply1
        profit1 = supply_APY_map1[supply1] * supply1
        profit2 = supply_APY_map2[supply2] * supply2
        profits.append(profit1 + profit2)
    
    # Get optimal values
    peak_supply1 = max_values['max_profit_supply1']
    peak_supply2 = max_values['max_profit_supply2']
    peak_profit = (max_values['max_profit_APY1'] * peak_supply1 + 
                  max_values['max_profit_APY2'] * peak_supply2)
    
    # Create figure
    plt.figure(figsize=(15, 8))
    
    # Plot profit line
    plt.plot(supplies, profits, 'g-', label='Total Profit', linewidth=2)
    
    # Add grid
    plt.grid(True, linestyle='-', alpha=0.3)
    
    # Set x-axis ticks using linspace for robustness with floats
    num_ticks = 6 # Adjust number of ticks as needed
    tick_positions = np.linspace(0, total_my_supply, num=num_ticks)
    plt.xticks(tick_positions)
    
    # Add peak point marker
    plt.plot(peak_supply1, peak_profit, 'ro', markersize=8)
    
    # Add peak point annotation
    plt.annotate(
        f'Peak Point\n'
        f'Fund Supply 1: {peak_supply1}\n'
        f'Fund Supply 2: {peak_supply2}\n'
        f'Total Profit: {peak_profit:.4f}',
        xy=(peak_supply1, peak_profit),
        xytext=(peak_supply1 + total_my_supply * 0.05, peak_profit * 1.1),
        arrowprops=dict(facecolor='black', shrink=0.05),
        bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.5')
    )
    
    # Add distribution points using the same linspace points
    for i in tick_positions:
        # Find the closest supply value in the calculated profits data
        # This is needed because linspace points might not exactly match the keys in supply_APY_map1
        closest_supply = min(supplies, key=lambda x: abs(x-i))
        if closest_supply in supply_APY_map1:
            supply2 = total_my_supply - closest_supply
            profit = supply_APY_map1[closest_supply] * closest_supply + supply_APY_map2.get(supply2, 0) * supply2 # Use .get for safety
            plt.annotate(
                f'F1:{closest_supply:.2f}, F2:{supply2:.2f}\n{profit:.4f}', # Format floats
                xy=(closest_supply, profit),
                xytext=(0, -20),
                textcoords='offset points',
                ha='center',
                va='top'
            )
    
    plt.xlabel('Fund 1 Supply')
    plt.ylabel('Total Profit')
    plt.title('Combined Profits')
    plt.legend(loc='upper right')
    
    plt.tight_layout()
    
    # Save to BytesIO for web response
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()
    
    return img_bytes

def plot_profit_distribution_3D(supply_APY_map1, supply_APY_map2, combined_APY, total_my_supply, max_values):
    """
    Create 3D chart showing total profit distribution across all three funds
    """
    # Get all supply combinations
    supplies = list(supply_APY_map1.keys())
    
    # Create mesh grid for 3D plot
    x = []
    y = []
    profits = []
    
    for supply1 in supplies:
        supply2 = total_my_supply - supply1
        profit1 = supply_APY_map1[supply1] * supply1
        profit2 = supply_APY_map2[supply2] * supply2
        x.append(supply1)
        y.append(supply2)
        profits.append(profit1 + profit2)
        print(f"supply1:{supply1} supply2:{supply2} profit1:{profit1} profit2:{profit2} total_profit:{profit1 + profit2}")
    
    # Get optimal values from max_values
    max_supply1 = max_values['max_profit_supply1']
    max_supply2 = max_values['max_profit_supply2']
    max_profit = (max_values['max_profit_APY1'] * max_supply1 + 
                  max_values['max_profit_APY2'] * max_supply2)

    
    # Create 3D figure
    fig = plt.figure(figsize=(15, 10))
    ax = fig.add_subplot(111, projection='3d')
    
    # Create surface plot
    surf = ax.plot3D(x, y, profits)
    
    # Add color bar
    
    # Add maximum profit point
    ax.scatter(max_supply1, max_supply2, max_profit, 
              color='red', s=100, label='Maximum Profit Point')
    
    # Add labels
    ax.set_xlabel('Fund 1 Supply')
    ax.set_ylabel('Fund 2 Supply')
    ax.set_zlabel('Total Profit')
    ax.set_title('3D Profit Distribution Across All Funds')
    ax.view_init(elev=30, azim=50)

    # Add legend
    ax.legend()
    
    # Add explanation text
    plt.figtext(0.5, 0.01, 
                'The surface shows how total profit changes with different supply distributions across two funds.\n'
                'The red point marks the combination that yields maximum profit.',
                ha='center', fontsize=10, style='italic')
    
    plt.tight_layout()
    
    # Save the chart to a file
    plt.savefig('profit_distribution_3d.png', dpi=300, bbox_inches='tight')
    
    # Save to BytesIO for web response
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()
    
    return img_bytes

@app.route('/api/profit-distribution', methods=['GET'])
def get_profit_distribution():
    """API endpoint that returns the profit distribution plot"""
    try:
        # Get parameters from either JSON body or query string
        if request.is_json:
            params = request.get_json()
        else:
            # Convert query parameters to dict and handle numeric values
            params = {}
            for key, value in request.args.items():
                try:
                    float_val = float(value)
                    params[key] = int(float_val) if float_val.is_integer() else float_val
                except ValueError:
                    params[key] = value

        if not params:
            return jsonify({'error': 'No parameters provided'}), 400
        
        # Validate and convert parameters
        try:
            validated_params = validate_parameters(params)
            num_funds = validated_params.pop('num_funds')
            total_supply = validated_params.pop('total_my_supply')
            fee_percentage = validated_params.pop('fee_percentage')
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        
        # Create appropriate fund parameters object and generate plot
        try:
            if num_funds == 2:
                # Use validated parameters from the request
                params = validated_params.copy()
                params['total_my_supply'] = total_supply
                params['fee_percentage'] = fee_percentage
                
                # Calculate max profits first
                max_values = calculate_max_profits(params)
                
                # Generate the profit distribution data
                supply_APY_map1, supply_APY_map2, combined_APY, total_profit = generate_supply_APY_maps(params)
                
                # Create the profit distribution plot with max_values
                img_bytes = plot_profit_distribution(supply_APY_map1, supply_APY_map2, combined_APY, params['total_my_supply'], max_values)
            else:
                return jsonify({'error': f'2D plot for {num_funds} funds is not supported yet'}), 400
        except Exception as e:
            return jsonify({'error': f'Plot generation error: {str(e)}'}), 500
            
        return send_file(
            img_bytes,
            mimetype='image/png',
            as_attachment=False,
            download_name='profit_distribution.png'
        )
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def calculate_theoretical_utilization_rate(total_supplied, our_supply, total_borrowed):
    """
    Calculate the theoretical utilization rate
    """
    utilization = (total_borrowed / (total_supplied + our_supply))
    return utilization

def calculate_borrow_APY(utilization, optimal_usage_ratio, base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2):
    """
    Calculate the borrow APY based on utilization rate and interest rate parameters
    
    Args:
        utilization: Current utilization rate
        optimal_usage_ratio: Optimal usage ratio for the fund
        base_variable_borrow_rate: Base variable borrow rate
        variable_rate_slope1: Slope for utilization below optimal point
        variable_rate_slope2: Slope for utilization above optimal point
    
    Returns:
        borrowAPY: Calculated borrow APY
    """
    if utilization <= optimal_usage_ratio:
        borrowAPY = base_variable_borrow_rate + (variable_rate_slope1 * utilization) / optimal_usage_ratio
    else:
        excess = (utilization - optimal_usage_ratio) / (1 - optimal_usage_ratio)
        borrowAPY = base_variable_borrow_rate + (variable_rate_slope1 * utilization)  + (variable_rate_slope2 * excess)
    
    return borrowAPY

def calculate_theoretical_supplied_apy(utilization, our_supply, total_supplied, total_borrowed, optimal_usage_ratio, 
                                     base_variable_borrow_rate, variable_rate_slope1, variable_rate_slope2,
                                     fee_percentage, token_price):
    """
    Calculate the theoretical variable APY based on utilization rate
    """
    borrowAPY = calculate_borrow_APY(utilization, optimal_usage_ratio, base_variable_borrow_rate, 
                                    variable_rate_slope1, variable_rate_slope2)

    total_borrowed_APY_USD = borrowAPY * total_borrowed * token_price
    total_supplied_APY = (total_borrowed_APY_USD * (1 - fee_percentage)) / (total_supplied + our_supply)
    
    return total_supplied_APY

def calculate_APY(our_supply, total_supplied, total_borrowed, optimal_usage_ratio, 
                    variable_rate_slope1, variable_rate_slope2, fee_percentage, token_price, rewards_per_year=0):
    """
    Calculate profit for a given supply value
    """
    # Calculate utilization rate
    utilization = calculate_theoretical_utilization_rate(total_supplied, our_supply, total_borrowed)
    
    # Calculate base APY from lending (existing logic)
    base_apy = calculate_theoretical_supplied_apy(utilization, our_supply, total_supplied, total_borrowed, optimal_usage_ratio, 
                                          0, variable_rate_slope1, variable_rate_slope2,
                                          fee_percentage, token_price)
    
    # Calculate rewards APY if rewards_per_year is provided
    rewards_apy = 0
    if rewards_per_year > 0:
        # Calculate reward APR using the formula: rewardPerYearUsd / SupplyUSD / 2
        rewards_apr = rewards_per_year / ((total_supplied + our_supply) * token_price) / 2
        # Convert rewards APR to APY
        rewards_apy = (1 + rewards_apr/365)**365 - 1
    
    # Total APY is the sum of base APY and rewards APY
    total_apy = base_apy + rewards_apy
        
    return total_apy


def generate_supply_APY_maps(params):
    """
    Generate maps of supply values and their corresponding profits for both funds
    """
    supply_APY_map1 = {}
    supply_APY_map2 = {}
    combined_APY = {}
    total_profit = {}
    
    # Get rewards parameters if available
    rewards_per_year1 = params.get('rewards_per_year1', 0)
    rewards_per_year2 = params.get('rewards_per_year2', 0)
    
    # Calculate dynamic step size to get around 100 points
    total_supply = params['total_my_supply']
    if total_supply < 1: # Cannot be 0 or negative
        step = 0.1 # Avoid division by zero and handle edge case
    elif total_supply < 10:
        step = 0.5 # Use step 0.5 for small supplies
    elif total_supply < 100:
        step = 1 # Use step 1 for small supplies
    else:
        step = max(1, total_supply // 100) # Ensure at least 1

    # Generate values for supply using the dynamic step and np.arange for float steps
    for supply1 in np.arange(0, total_supply + step, step): # Use total_supply + step for endpoint inclusion
        # Ensure supply1 does not exceed total_supply due to float precision
        supply1 = min(supply1, total_supply)
        supply2 = total_supply - supply1  # Complementary supply for second fund
        
        # Calculate profits for both funds
        APY1 = calculate_APY(supply1, params['total_supplied1'], params['total_borrowed1'], params['optimal_usage_ratio1'], 
                                 params['variable_rate_slope1_1'], params['variable_rate_slope2_1'],
                                 params['fee_percentage'], params['token_price1'], rewards_per_year1)
        APY2 = calculate_APY(supply2, params['total_supplied2'], params['total_borrowed2'], params['optimal_usage_ratio2'],
                                 params['variable_rate_slope1_2'], params['variable_rate_slope2_2'],
                                 params['fee_percentage'], params['token_price2'], rewards_per_year2)
        
        # Store results in respective maps
        supply_APY_map1[supply1] = APY1
        supply_APY_map2[supply2] = APY2

        combined_APY[supply1] = 0
        if (supply1 > 0):
            combined_APY[supply1] += APY1
        if (supply2 > 0):
            combined_APY[supply1] += APY2

        total_profit[supply1] = APY1 * supply1 + APY2 * supply2

        # print(f"supply1:{supply1} APY1:{APY1:2f} Profit1:{APY1 * supply1:0f} supply2:{supply2} APY2:{APY2:0f} Profit2:{APY2 * supply2:0f} total_profit {total_profit[supply1]:0f}")
    
    return supply_APY_map1, supply_APY_map2, combined_APY, total_profit

def calculate_max_profits(params):
    """Helper function to calculate all max profit values"""
    # Generate the supply-profit maps for both funds
    result_map1, result_map2, combined_APY, total_profit = generate_supply_APY_maps(params)
    
    # Find maximum profit combination
    max_profit_supply1 = max(total_profit.items(), key=lambda x: x[1])[0]
    max_profit_supply2 = params['total_my_supply'] - max_profit_supply1
    max_profit_APY1 = result_map1[max_profit_supply1]
    max_profit_APY2 = result_map2[max_profit_supply2]
    
    # Calculate utilization rates
    maxUtilization1 = (params['total_borrowed1'] / (params['total_supplied1'] + max_profit_supply1)) * 100
    maxUtilization2 = (params['total_borrowed2'] / (params['total_supplied2'] + max_profit_supply2)) * 100
    
    return {
        'max_profit_supply1': max_profit_supply1,
        'max_profit_supply2': max_profit_supply2,
        'max_profit_APY1': max_profit_APY1,
        'max_profit_APY2': max_profit_APY2,
        'maxUtilization1': maxUtilization1,
        'maxUtilization2': maxUtilization2
    }


@app.route('/api/profit-distribution-3d', methods=['GET'])
def get_profit_distribution_3D():
    """API endpoint that returns the 3D profit distribution plot"""
    try:
        # Get parameters from either JSON body or query string
        if request.is_json:
            params = request.get_json()
        else:
            # Convert query parameters to dict and handle numeric values
            params = {}
            for key, value in request.args.items():
                try:
                    float_val = float(value)
                    params[key] = int(float_val) if float_val.is_integer() else float_val
                except ValueError:
                    params[key] = value

        if not params:
            return jsonify({'error': 'No parameters provided'}), 400

        # Validate and convert parameters
        try:
            validated_params = validate_parameters(params)
            logging.info(f"get_profit_distribution_3D: validated_params after validation: {validated_params}") # LOG 1

            num_funds = validated_params.pop('num_funds')
            total_supply = validated_params.pop('total_my_supply')
            fee_percentage = validated_params.pop('fee_percentage')

            # Get rewards parameters for each fund if available
            rewards_params = {}
            # Use num_funds detected by validate_parameters
            for i in range(1, num_funds + 1):
                key = f'rewards_per_year{i}'
                if key in validated_params:
                     # Pop them so they aren't passed directly later if constructor doesn't expect them
                    rewards_params[key] = validated_params.pop(key)
            
            logging.info(f"get_profit_distribution_3D: rewards_params after extraction: {rewards_params}") # LOG 2
            logging.info(f"get_profit_distribution_3D: validated_params after extraction: {validated_params}") # LOG 3

        except Exception as e:
            # Log the validation error details
            logging.error(f"Parameter validation failed: {str(e)}")
            return jsonify({'error': f'Parameter validation failed: {str(e)}'}), 400

        # Create appropriate fund parameters object and generate plot
        try:
            if num_funds == 2:
                # Use validated parameters from the request
                # Note: calculate_max_profits and generate_supply_APY_maps use dict, not FundParameters object
                param_dict = validated_params.copy()
                param_dict['total_my_supply'] = total_supply
                param_dict['fee_percentage'] = fee_percentage
                # Add rewards back if they exist for this case
                param_dict.update(rewards_params)

                # Calculate max profits first
                max_values = calculate_max_profits(param_dict)

                # Generate the profit distribution data
                supply_APY_map1, supply_APY_map2, combined_APY, total_profit = generate_supply_APY_maps(param_dict)
                # Create the profit distribution plot with max_values
                img_bytes = plot_profit_distribution_3D(supply_APY_map1, supply_APY_map2, combined_APY, total_supply, max_values) # Pass total_supply

            elif num_funds == 3:
                # Create FundParameters3 object, passing rewards
                fund_params = FundParameters3(
                    total_borrowed1=validated_params['total_borrowed1'],
                    total_supplied1=validated_params['total_supplied1'],
                    optimal_usage_ratio1=validated_params['optimal_usage_ratio1'],
                    variable_rate_slope1_1=validated_params['variable_rate_slope1_1'],
                    variable_rate_slope2_1=validated_params['variable_rate_slope2_1'],
                    token_price1=validated_params['token_price1'],
                    total_borrowed2=validated_params['total_borrowed2'],
                    total_supplied2=validated_params['total_supplied2'],
                    optimal_usage_ratio2=validated_params['optimal_usage_ratio2'],
                    variable_rate_slope1_2=validated_params['variable_rate_slope1_2'],
                    variable_rate_slope2_2=validated_params['variable_rate_slope2_2'],
                    token_price2=validated_params['token_price2'],
                    total_borrowed3=validated_params['total_borrowed3'],
                    total_supplied3=validated_params['total_supplied3'],
                    optimal_usage_ratio3=validated_params['optimal_usage_ratio3'],
                    variable_rate_slope1_3=validated_params['variable_rate_slope1_3'],
                    variable_rate_slope2_3=validated_params['variable_rate_slope2_3'],
                    token_price3=validated_params['token_price3'],
                    fee_percentage=fee_percentage,
                    **rewards_params # Pass extracted rewards parameters
                )
                img_bytes = plot_profit_distribution_3D_3funds(total_supply, fund_params)
            elif num_funds == 4:
                 # Create FundParameters4 object, passing rewards
                fund_params = FundParameters4(
                    total_borrowed1=validated_params['total_borrowed1'],
                    total_supplied1=validated_params['total_supplied1'],
                    optimal_usage_ratio1=validated_params['optimal_usage_ratio1'],
                    variable_rate_slope1_1=validated_params['variable_rate_slope1_1'],
                    variable_rate_slope2_1=validated_params['variable_rate_slope2_1'],
                    token_price1=validated_params['token_price1'],
                    total_borrowed2=validated_params['total_borrowed2'],
                    total_supplied2=validated_params['total_supplied2'],
                    optimal_usage_ratio2=validated_params['optimal_usage_ratio2'],
                    variable_rate_slope1_2=validated_params['variable_rate_slope1_2'],
                    variable_rate_slope2_2=validated_params['variable_rate_slope2_2'],
                    token_price2=validated_params['token_price2'],
                    total_borrowed3=validated_params['total_borrowed3'],
                    total_supplied3=validated_params['total_supplied3'],
                    optimal_usage_ratio3=validated_params['optimal_usage_ratio3'],
                    variable_rate_slope1_3=validated_params['variable_rate_slope1_3'],
                    variable_rate_slope2_3=validated_params['variable_rate_slope2_3'],
                    token_price3=validated_params['token_price3'],
                    total_borrowed4=validated_params['total_borrowed4'],
                    total_supplied4=validated_params['total_supplied4'],
                    optimal_usage_ratio4=validated_params['optimal_usage_ratio4'],
                    variable_rate_slope1_4=validated_params['variable_rate_slope1_4'],
                    variable_rate_slope2_4=validated_params['variable_rate_slope2_4'],
                    token_price4=validated_params['token_price4'],
                    fee_percentage=fee_percentage,
                    **rewards_params # Pass extracted rewards parameters
                )
                img_bytes = plot_profit_distribution_3D_4funds(total_supply, fund_params)
            else:
                # Handle cases with unsupported number of funds
                return jsonify({'error': f'3D plot for {num_funds} funds is not supported yet'}), 400
        except Exception as e:
            # Log the plot generation error details
            logging.error(f"Plot generation error: {str(e)}", exc_info=True)
            return jsonify({'error': f'Plot generation error: {str(e)}'}), 500

        return send_file(
            img_bytes,
            mimetype='image/png',
            as_attachment=False,
            download_name='profit_distribution_3d.png'
        )

    except Exception as e:
        # Log any other unexpected errors
        logging.error(f"Unexpected error in /api/profit-distribution-3d: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/max-profits', methods=['GET'])
def get_max_profits():
    """API endpoint that returns max profit values for any number of funds"""
    try:
        # Get parameters from either JSON body or query string
        if request.is_json:
            params = request.get_json()
        else:
            # Convert query parameters to dict and handle numeric values
            params = {}
            for key, value in request.args.items():
                try:
                    float_val = float(value)
                    params[key] = int(float_val) if float_val.is_integer() else float_val
                except ValueError:
                    params[key] = value

        if not params:
            return jsonify({'error': 'No parameters provided'}), 400
        
        # Validate and convert parameters
        try:
            validated_params = validate_parameters(params)
            num_funds = validated_params.pop('num_funds')
            total_supply = validated_params.pop('total_my_supply')
            fee_percentage = validated_params.pop('fee_percentage')
            
            # Get rewards parameters for each fund if available
            rewards_params = {}
            for i in range(1, num_funds + 1):
                key = f'rewards_per_year{i}'
                if key in validated_params:
                    rewards_params[key] = validated_params.pop(key)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        
        # Calculate results based on number of funds
        try:
            if num_funds == 2:
                fund_params = FundParameters2(
                    total_borrowed1=validated_params['total_borrowed1'],
                    total_supplied1=validated_params['total_supplied1'],
                    optimal_usage_ratio1=validated_params['optimal_usage_ratio1'],
                    variable_rate_slope1_1=validated_params['variable_rate_slope1_1'],
                    variable_rate_slope2_1=validated_params['variable_rate_slope2_1'],
                    token_price1=validated_params['token_price1'],
                    total_borrowed2=validated_params['total_borrowed2'],
                    total_supplied2=validated_params['total_supplied2'],
                    optimal_usage_ratio2=validated_params['optimal_usage_ratio2'],
                    variable_rate_slope1_2=validated_params['variable_rate_slope1_2'],
                    variable_rate_slope2_2=validated_params['variable_rate_slope2_2'],
                    token_price2=validated_params['token_price2'],
                    fee_percentage=fee_percentage,
                    **rewards_params  # Pass rewards parameters
                )
                result = calculate_distribution_2(total_supply, fund_params)
                print("result2", result)
            elif num_funds == 3:
                fund_params = FundParameters3(
                    total_borrowed1=validated_params['total_borrowed1'],
                    total_supplied1=validated_params['total_supplied1'],
                    optimal_usage_ratio1=validated_params['optimal_usage_ratio1'],
                    variable_rate_slope1_1=validated_params['variable_rate_slope1_1'],
                    variable_rate_slope2_1=validated_params['variable_rate_slope2_1'],
                    token_price1=validated_params['token_price1'],
                    total_borrowed2=validated_params['total_borrowed2'],
                    total_supplied2=validated_params['total_supplied2'],
                    optimal_usage_ratio2=validated_params['optimal_usage_ratio2'],
                    variable_rate_slope1_2=validated_params['variable_rate_slope1_2'],
                    variable_rate_slope2_2=validated_params['variable_rate_slope2_2'],
                    token_price2=validated_params['token_price2'],
                    total_borrowed3=validated_params['total_borrowed3'],
                    total_supplied3=validated_params['total_supplied3'],
                    optimal_usage_ratio3=validated_params['optimal_usage_ratio3'],
                    variable_rate_slope1_3=validated_params['variable_rate_slope1_3'],
                    variable_rate_slope2_3=validated_params['variable_rate_slope2_3'],
                    token_price3=validated_params['token_price3'],
                    fee_percentage=fee_percentage,
                    **rewards_params  # Pass rewards parameters
                )
                result = calculate_distribution_3(total_supply, fund_params)
            elif num_funds == 4:
                fund_params = FundParameters4(
                    total_borrowed1=validated_params['total_borrowed1'],
                    total_supplied1=validated_params['total_supplied1'],
                    optimal_usage_ratio1=validated_params['optimal_usage_ratio1'],
                    variable_rate_slope1_1=validated_params['variable_rate_slope1_1'],
                    variable_rate_slope2_1=validated_params['variable_rate_slope2_1'],
                    token_price1=validated_params['token_price1'],
                    total_borrowed2=validated_params['total_borrowed2'],
                    total_supplied2=validated_params['total_supplied2'],
                    optimal_usage_ratio2=validated_params['optimal_usage_ratio2'],
                    variable_rate_slope1_2=validated_params['variable_rate_slope1_2'],
                    variable_rate_slope2_2=validated_params['variable_rate_slope2_2'],
                    token_price2=validated_params['token_price2'],
                    total_borrowed3=validated_params['total_borrowed3'],
                    total_supplied3=validated_params['total_supplied3'],
                    optimal_usage_ratio3=validated_params['optimal_usage_ratio3'],
                    variable_rate_slope1_3=validated_params['variable_rate_slope1_3'],
                    variable_rate_slope2_3=validated_params['variable_rate_slope2_3'],
                    token_price3=validated_params['token_price3'],
                    total_borrowed4=validated_params['total_borrowed4'],
                    total_supplied4=validated_params['total_supplied4'],
                    optimal_usage_ratio4=validated_params['optimal_usage_ratio4'],
                    variable_rate_slope1_4=validated_params['variable_rate_slope1_4'],
                    variable_rate_slope2_4=validated_params['variable_rate_slope2_4'],
                    token_price4=validated_params['token_price4'],
                    fee_percentage=fee_percentage,
                    **rewards_params  # Pass rewards parameters
                )
                result = calculate_distribution_4(total_supply, fund_params)
            else:
                return jsonify({'error': f'Calculation for {num_funds} funds is not supported yet'}), 400
        except Exception as e:
            return jsonify({'error': f'Calculation error: {str(e)}'}), 500
            
        return jsonify(result)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def plot_profit_distribution_2funds(total_supply, params):
    """
    Create 2D chart showing total profit distribution for 2 funds
    
    Args:
        total_supply: Total supply to distribute
        params: FundParameters2 object containing fund parameters
    
    Returns:
        BytesIO object containing the generated PNG image
    """
    # Generate the necessary data maps
    # Note: params needs to be a dictionary compatible with generate_supply_APY_maps
    # The incoming 'params' is a FundParameters2 object, so we need to convert it or adjust.
    # For now, assuming the structure passed allows direct access like params['total_my_supply'] etc.
    # If FundParameters2 doesn't work, this needs adjustment.
    param_dict = {
        'total_my_supply': total_supply,
        'fee_percentage': params.fee_percentage,
        'total_supplied1': params.fund1['total_supplied'],
        'total_borrowed1': params.fund1['total_borrowed'],
        'optimal_usage_ratio1': params.fund1['optimal_usage_ratio'],
        'variable_rate_slope1_1': params.fund1['variable_rate_slope1'],
        'variable_rate_slope2_1': params.fund1['variable_rate_slope2'],
        'token_price1': params.fund1['token_price'],
        'rewards_per_year1': params.fund1.get('rewards_per_year', 0),
        'total_supplied2': params.fund2['total_supplied'],
        'total_borrowed2': params.fund2['total_borrowed'],
        'optimal_usage_ratio2': params.fund2['optimal_usage_ratio'],
        'variable_rate_slope1_2': params.fund2['variable_rate_slope1'],
        'variable_rate_slope2_2': params.fund2['variable_rate_slope2'],
        'token_price2': params.fund2['token_price'],
        'rewards_per_year2': params.fund2.get('rewards_per_year', 0)
    }
    supply_APY_map1, supply_APY_map2, combined_APY, total_profit_map = generate_supply_APY_maps(param_dict)
    supplies_calculated = list(supply_APY_map1.keys()) # Get the keys generated by the function
    
    # Calculate profits for each distribution using the generated maps
    profits = [total_profit_map[s] for s in supplies_calculated]
    
    # Calculate optimal distribution
    result = calculate_distribution_2(total_supply, params)
    peak_supply1 = result['fund1_supply']
    peak_supply2 = result['fund2_supply']
    peak_profit = result['total_profit']
    
    # Create figure
    plt.figure(figsize=(15, 8))
    
    # Plot profit line
    plt.plot(supplies_calculated, profits, 'g-', label='Total Profit', linewidth=2)
    
    # Add grid
    plt.grid(True, linestyle='-', alpha=0.3)
    
    # Set x-axis ticks using linspace for robustness with floats
    num_ticks = 6 # Adjust number of ticks as needed
    tick_positions = np.linspace(0, total_supply, num=num_ticks)
    plt.xticks(tick_positions)
    
    # Add peak point marker
    plt.plot(peak_supply1, peak_profit, 'ro', markersize=8)
    
    # Add peak point annotation
    plt.annotate(
        f'Peak Point\n'
        f'Fund Supply 1: {peak_supply1:.2f}\n'
        f'Fund Supply 2: {peak_supply2:.2f}\n'
        f'Total Profit: {peak_profit:.4f}',
        xy=(peak_supply1, peak_profit),
        xytext=(peak_supply1 + total_supply * 0.05, peak_profit * 1.1),
        arrowprops=dict(facecolor='black', shrink=0.05),
        bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.5')
    )
    
    # Add distribution points using the same linspace points
    for i in tick_positions:
        # Find the closest supply value in the calculated profits data
        closest_supply = min(supplies_calculated, key=lambda x: abs(x-i))
        if closest_supply in supply_APY_map1:
            supply2 = total_supply - closest_supply # Use the function arg total_supply
            profit = supply_APY_map1[closest_supply] * closest_supply + supply_APY_map2.get(supply2, 0) * supply2 # Use .get for safety
            plt.annotate(
                f'F1:{closest_supply:.2f}, F2:{supply2:.2f}\n{profit:.4f}', # Format floats
                xy=(closest_supply, profit),
                xytext=(0, -20),
                textcoords='offset points',
                ha='center',
                va='top'
            )
    
    plt.xlabel('Fund 1 Supply')
    plt.ylabel('Total Profit')
    plt.title('Combined Profits (2 Funds)')
    plt.legend(loc='upper right')
    
    plt.tight_layout()
    
    # Save to BytesIO for web response
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()
    
    return img_bytes

def plot_profit_distribution_3D_2funds(total_supply, params):
    """
    Create 3D chart showing total profit distribution for 2 funds
    
    Args:
        total_supply: Total supply to distribute
        params: FundParameters2 object containing fund parameters
    
    Returns:
        BytesIO object containing the generated PNG image
    """
    # Create supply points
    supplies = np.linspace(0, total_supply, 50)
    
    # Create mesh grid for 3D plot
    x = []
    y = []
    profits = []
    
    for supply1 in supplies:
        supply2 = total_supply - supply1
        profit1 = f_pool1_2(supply1, params) * supply1
        profit2 = f_pool2_2(supply2, params) * supply2
        x.append(supply1)
        y.append(supply2)
        profits.append(profit1 + profit2)
    
    # Calculate optimal distribution
    result = calculate_distribution_2(total_supply, params)
    max_supply1 = result['fund1_supply']
    max_supply2 = result['fund2_supply']
    max_profit = result['total_profit']
    
    # Create 3D figure
    fig = plt.figure(figsize=(15, 10))
    ax = fig.add_subplot(111, projection='3d')
    
    # Create surface plot
    surf = ax.plot3D(x, y, profits)
    
    # Add maximum profit point
    ax.scatter(max_supply1, max_supply2, max_profit, 
              color='red', s=100, label='Maximum Profit Point')
    
    # Add labels
    ax.set_xlabel('Fund 1 Supply')
    ax.set_ylabel('Fund 2 Supply')
    ax.set_zlabel('Total Profit')
    ax.set_title('3D Profit Distribution (2 Funds)')
    ax.view_init(elev=30, azim=50)

    # Add legend
    ax.legend()
    
    # Add explanation text
    plt.figtext(0.5, 0.01, 
                'The surface shows how total profit changes with different supply distributions across two funds.\n'
                'The red point marks the combination that yields maximum profit.',
                ha='center', fontsize=10, style='italic')
    
    plt.tight_layout()
    
    # Save to BytesIO for web response
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()
    
    return img_bytes

def plot_profit_distribution_3D_3funds(total_supply, params):
    """
    Create 3D chart showing total profit distribution for 3 funds
    
    Args:
        total_supply: Total supply to distribute
        params: FundParameters3 object containing fund parameters
    
    Returns:
        BytesIO object containing the generated PNG image
    """
    # Log received parameters to check for rewards
    logging.info(f"plot_profit_distribution_3D_3funds received total_supply: {total_supply}")
    logging.info(f"plot_profit_distribution_3D_3funds received params.fund1: {params.fund1}")
    logging.info(f"plot_profit_distribution_3D_3funds received params.fund2: {params.fund2}")
    logging.info(f"plot_profit_distribution_3D_3funds received params.fund3: {params.fund3}")

    # Create supply points - increased resolution to 50 points
    num_points = 50
    step = total_supply / (num_points - 1)
    supplies = np.linspace(0, total_supply, num_points)
    
    # Create mesh grid for 3D plot
    x = []  # Reserve 1
    y = []  # Reserve 2
    z = []  # Reserve 3
    profits = []  # For color gradient
    
    for supply1 in supplies:
        for supply2 in supplies:
            if supply1 + supply2 <= total_supply:
                supply3 = total_supply - supply1 - supply2
                if supply3 >= 0:  # Only add valid points
                    profit1 = f_pool1_3(supply1, params) * supply1
                    profit2 = f_pool2_3(supply2, params) * supply2
                    profit3 = f_pool3_3(supply3, params) * supply3
                    x.append(supply1)
                    y.append(supply2)
                    z.append(supply3)
                    profits.append(profit1 + profit2 + profit3)
    
    # Calculate optimal distribution
    result = calculate_distribution_3(total_supply, params)
    max_supply1 = result['reserve1_supply'] # Use correct key
    max_supply2 = result['reserve2_supply'] # Use correct key
    max_supply3 = result['reserve3_supply'] # Use correct key
    max_profit = result['total_profit']
    
    # Create 3D figure
    fig = plt.figure(figsize=(15, 10))
    ax = fig.add_subplot(111, projection='3d')
    
    # Create scatter plot with color gradient based on total profit
    scatter = ax.scatter(x, y, z, c=profits, cmap='viridis', alpha=0.6)  # Added some transparency
    
    # Add maximum profit point
    ax.scatter(max_supply1, max_supply2, max_supply3, 
              color='red', s=100, label='Maximum Profit Point')
    
    # Add color bar
    plt.colorbar(scatter, label='Total Profit')
    
    # Add labels and set ticks
    ax.set_xlabel('Reserve 1 Supply')
    ax.set_ylabel('Reserve 2 Supply')
    ax.set_zlabel('Reserve 3 Supply')
    
    # Set ticks at regular intervals
    tick_count = 5
    tick_step = total_supply / tick_count
    ticks = np.arange(0, total_supply + tick_step, tick_step)
    ax.set_xticks(ticks)
    ax.set_yticks(ticks)
    ax.set_zticks(ticks)
    
    # Format tick labels to be more readable
    def format_tick(x, p):
        if x >= 1e6:
            return f'{x/1e6:.1f}M'
        elif x >= 1e3:
            return f'{x/1e3:.0f}K'
        return str(int(x))
    
    ax.xaxis.set_major_formatter(plt.FuncFormatter(format_tick))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(format_tick))
    ax.zaxis.set_major_formatter(plt.FuncFormatter(format_tick))
    
    ax.set_title('3D Profit Distribution (3 Reserves)')
    ax.view_init(elev=30, azim=50)

    # Add legend
    ax.legend()
    
    # Add explanation text
    plt.figtext(0.5, 0.01, 
                'The scatter plot shows how total profit changes with different supply distributions across three reserves.\n'
                'The red point marks the combination that yields maximum profit.',
                ha='center', fontsize=10, style='italic')
    
    plt.tight_layout()
    
    # Save to BytesIO for web response
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()
    
    return img_bytes

def plot_profit_distribution_3D_4funds(total_supply, params):
    """
    Create 3D chart showing total profit distribution for 4 funds
    
    Args:
        total_supply: Total supply to distribute
        params: FundParameters4 object containing fund parameters
    
    Returns:
        BytesIO object containing the generated PNG image
    """
    # Create supply points
    supplies = np.linspace(0, total_supply, 15)
    
    # Create mesh grid for 3D plot
    x = []
    y = []
    z = []
    profits = []
    
    for supply1 in supplies:
        for supply2 in supplies:
            if supply1 + supply2 <= total_supply:
                for supply3 in supplies:
                    if supply1 + supply2 + supply3 <= total_supply:
                        supply4 = total_supply - supply1 - supply2 - supply3
                        profit1 = f_pool1_4(supply1, params) * supply1
                        profit2 = f_pool2_4(supply2, params) * supply2
                        profit3 = f_pool3_4(supply3, params) * supply3
                        profit4 = f_pool4_4(supply4, params) * supply4
                        x.append(supply1)
                        y.append(supply2)
                        z.append(supply3)
                        profits.append(profit1 + profit2 + profit3 + profit4)
    
    # Calculate optimal distribution
    result = calculate_distribution_4(total_supply, params)
    max_supply1 = result['fund1_supply']
    max_supply2 = result['fund2_supply']
    max_supply3 = result['fund3_supply']
    max_supply4 = result['fund4_supply']
    max_profit = result['total_profit']
    
    # Create 3D figure
    fig = plt.figure(figsize=(15, 10))
    ax = fig.add_subplot(111, projection='3d')
    
    # Create scatter plot: x=supply1, y=supply2, z=supply3, color=profit
    scatter = ax.scatter(x, y, z, c=profits, cmap='viridis', alpha=0.6)
    
    # Add maximum profit point
    # Note: Plotting the 4D optimal point in 3D space requires choosing which 3 supply dimensions to show.
    # Here we plot supply1, supply2, supply3 of the max point.
    ax.scatter(max_supply1, max_supply2, max_supply3, 
              color='red', s=100, label=f'Max Profit Point\n(S4={max_supply4:.2f})') # Show S4 in label
    
    # Add color bar
    plt.colorbar(scatter, label='Total Profit')
    
    # Add labels
    ax.set_xlabel('Fund 1 Supply')
    ax.set_ylabel('Fund 2 Supply')
    ax.set_zlabel('Fund 3 Supply') # Changed z-label to Fund 3
    ax.set_title('3D Profit Distribution (4 Funds - Showing S1, S2, S3)') # Updated title
    ax.view_init(elev=30, azim=50)

    # Add legend
    ax.legend()
    
    # Add explanation text
    plt.figtext(0.5, 0.01, 
                'The scatter plot shows profit (color) for different supply distributions across the first three funds (axes).\n'
                f'Supply for Fund 4 is implicitly defined (Total - S1 - S2 - S3). The red point marks the combination yielding maximum total profit.',
                ha='center', fontsize=10, style='italic')
    
    plt.tight_layout()
    
    # Save to BytesIO for web response
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', dpi=300, bbox_inches='tight')
    img_bytes.seek(0)
    plt.close()
    
    return img_bytes


@app.route('/api/fetch-pool-data', methods=['POST'])
def fetch_pool_data():
    """
    Fetches all necessary data for pools and reserves calculation
    """
    try:
        data = request.get_json()
        
        if not data or 'wallet_address' not in data or 'funds' not in data:
            return jsonify({'error': 'Missing required parameters'}), 400
            
        wallet_address = data['wallet_address']
        funds = data['funds']
        
        # Validate input funds
        if not isinstance(funds, list) or len(funds) == 0:
            return jsonify({'error': 'Funds must be a non-empty list'}), 400
        
        logging.info(f"Processing {len(funds)} funds for wallet {wallet_address}")
        
        result = {
            'pools': [],
            'reserves': []
        }
        
        # Track processing results for validation
        processed_count = 0
        failed_funds = []
        
        for i, fund in enumerate(funds):
            logging.info(f"Processing fund {i+1}/{len(funds)}: {fund.get('type', 'unknown')} - {fund.get('address', 'unknown')[:10]}...")
            
            try:
                processed_result = ProtocolFactory.process_fund(fund, wallet_address)
                
                if processed_result:
                    data_type, processed_data = processed_result
                    
                    if data_type == 'pool':
                        result['pools'].append(processed_data)
                        logging.info(f"Successfully processed pool: {processed_data.get('name', 'unknown')}")
                    elif data_type == 'reserve':
                        result['reserves'].append(processed_data)
                        logging.info(f"Successfully processed reserve: {processed_data.get('name', 'unknown')}")
                    
                    processed_count += 1
                else:
                    failed_funds.append({
                        'index': i,
                        'fund': fund,
                        'reason': 'ProtocolFactory.process_fund returned None'
                    })
                    logging.warning(f"Failed to process fund {i+1}: {fund.get('type', 'unknown')} - {fund.get('address', 'unknown')[:10]}")
                    
            except Exception as e:
                failed_funds.append({
                    'index': i,
                    'fund': fund,
                    'reason': f'Exception: {str(e)}'
                })
                logging.error(f"Exception processing fund {i+1}: {fund.get('type', 'unknown')} - {fund.get('address', 'unknown')[:10]} - Error: {str(e)}")
        
        # Validation: Check if we processed all funds
        total_processed = len(result['pools']) + len(result['reserves'])
        expected_count = len(funds)
        
        logging.info(f"Processing complete: {total_processed}/{expected_count} funds processed successfully")
        
        if total_processed != expected_count:
            error_msg = f"Data completeness validation failed. Expected {expected_count} funds, but only {total_processed} were processed successfully."
            logging.error(error_msg)
            
            # Add detailed failure information to help debugging
            failure_details = {
                'expected_count': expected_count,
                'actual_processed': total_processed,
                'failed_funds': failed_funds,
                'successful_pools': len(result['pools']),
                'successful_reserves': len(result['reserves'])
            }
            
            return jsonify({
                'error': error_msg,
                'failure_details': failure_details,
                'partial_data': result
            }), 400
        
        # Additional validation: Ensure we have at least some data
        if not result['pools'] and not result['reserves']:
            return jsonify({'error': 'No valid pool or reserve data provided'}), 400
        
        logging.info(f"Successfully returning data: {len(result['pools'])} pools, {len(result['reserves'])} reserves")
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error in fetch-pool-data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/calculate-optimal-allocation', methods=['POST'])
def calculate_optimal_allocation():
    """
    Calculates optimal allocation based on prepared data
    """
    try:
        data = request.get_json()
        
        if not data or 'total_funds' not in data:
            return jsonify({'error': 'Missing total_funds parameter'}), 400
            
        total_funds = float(data['total_funds'])
        
        # Convert pools and reserves to investment data format
        investments_data = []
        
        # Process pools
        for pool in data.get('pools', []):
            pool_investment = {
                'type': 'pool',
                'name': pool['name'] + ' ' + pool['source'],
                'daily_fee': pool['daily_fee'],
                'pool_distribution': pool['pool_distribution'],
                'id': pool.get('id', '0x0000000000000000000000000000000000000000')  # Add ID for pools (usually not aToken, but for consistency)
            }
            
            # Add reward info if available
            if 'reward_per_day' in pool and 'reward_token_price' in pool:
                pool_investment['reward_per_day'] = pool['reward_per_day']
                pool_investment['reward_token_price'] = pool['reward_token_price']
                
            investments_data.append(pool_investment)
            
        # Process reserves
        for reserve in data.get('reserves', []):
            reserve_investment = {
                'type': 'reserve',
                'name': reserve.get('name', 'Reserve') + ' ' + reserve.get('source', ''),
                'id': reserve.get('id', '0x0000000000000000000000000000000000000000')  # Extract aToken address or use default
            }
            for key, value in reserve.items():
                if key not in reserve_investment:
                    reserve_investment[key] = value
            investments_data.append(reserve_investment)
            
        if not investments_data:
            return jsonify({'error': 'No valid investment data provided'}), 400
            
        # Create parameters and calculate optimal distribution
        params = create_investment_parameters(investments_data)
        result = calculate_optimal_distribution(total_funds, params)
        
        # Ensure all details have ID field
        details = result.get('details', [])
        for i, allocation in enumerate(details):
            # Get ID from original investment data
            if i < len(investments_data):
                allocation['id'] = investments_data[i].get('id', '0x000')
            else:
                allocation['id'] = '0x0000000000000000000000000000000000000000'
        
        # If total_funds is 0, set all allocations to 0 but keep APY calculations
        if total_funds == 0:
            for allocation in details:
                allocation['allocated_amount'] = 0
                allocation['percentage'] = 0
                allocation['expected_profit'] = 0
            
            # Update main result fields with zeros
            for i, allocation in enumerate(details, 1):
                investment_type = allocation['type']
                result[f'{investment_type}{i}_supply'] = 0
            
            result['total_profit'] = 0
            return jsonify(result)
        
        # Get minimum allocation percentage (default to 1%)
        min_allocation_percent = float(data.get('min_allocation_percent', 1.0))
        min_allocation_threshold = total_funds * (min_allocation_percent / 100.0)
        
        # If total_funds is too small to satisfy minimum allocation threshold,
        # allocate everything to the highest APY investment
        if total_funds < min_allocation_threshold * len(investments_data):
            if details:
                # Find investment with highest APY
                best_investment = max(details, key=lambda x: x.get('total_apy', 0))
                best_investment['allocated_amount'] = total_funds
                best_investment['percentage'] = 100
                best_investment['expected_profit'] = best_investment.get('total_apy', 0) * total_funds / 100
                
                # Set others to zero
                for allocation in details:
                    if allocation != best_investment:
                        allocation['allocated_amount'] = 0
                        allocation['percentage'] = 0
                        allocation['expected_profit'] = 0
                
                # Update main result fields
                for i, allocation in enumerate(details, 1):
                    investment_type = allocation['type']
                    result[f'{investment_type}{i}_supply'] = allocation['allocated_amount']
                
                result['total_profit'] = best_investment['expected_profit']
                return jsonify(result)
        
        # Apply minimum allocation threshold
        # Step 1: Identify investments below threshold
        reallocate_amount = 0
        valid_allocations = []
        
        for allocation in details:
            if allocation['allocated_amount'] < min_allocation_threshold:
                # Add this amount to be reallocated
                reallocate_amount += allocation['allocated_amount']
                # Set to zero
                allocation['allocated_amount'] = 0
                allocation['percentage'] = 0
                if 'expected_profit' in allocation:
                    allocation['expected_profit'] = 0
            else:
                valid_allocations.append(allocation)
        
        # Step 2: Redistribute funds to remaining valid allocations
        if reallocate_amount > 0 and valid_allocations:
            # Calculate total valid allocation amount for proportional distribution
            total_valid_amount = sum(alloc['allocated_amount'] for alloc in valid_allocations)
            
            # Distribute reallocated funds proportionally
            for allocation in valid_allocations:
                proportion = allocation['allocated_amount'] / total_valid_amount
                additional_amount = reallocate_amount * proportion
                
                # Update allocation
                allocation['allocated_amount'] += additional_amount
                allocation['percentage'] = (allocation['allocated_amount'] / total_funds) * 100
                
                # Update profit if applicable - FIXED: всюди використовуємо total_apy замість expected_apy
                if 'total_apy' in allocation:
                    allocation['expected_profit'] = allocation['total_apy'] * allocation['allocated_amount'] / 100
        
        # Update main result fields
        for i, allocation in enumerate(details, 1):
            investment_type = allocation['type']
            result[f'{investment_type}{i}_supply'] = allocation['allocated_amount']
        
        # Recalculate total profit
        result['total_profit'] = sum(alloc.get('expected_profit', 0) for alloc in details)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/defillama', methods=['GET', 'POST'])
def get_defillama_data():
    """
    API endpoint for getting pool information from DefiLlama.
    Supports optional filtering by chain, project, symbol.
    Only returns pools with valid APY values (greater than 0).
    
    For POST requests, example JSON body:
    {
        "chain": "ethereum,polygon",
        "project": "aave,compound",
        "symbol": "usdc,usdt,dai"
    }
    
    For GET requests, use query parameters:
    /api/defillama?chain=ethereum,polygon&project=aave,compound&symbol=usdc,usdt,dai
    """
    try:
        # Get filter parameters from JSON body (POST) or query parameters (GET)
        if request.method == 'POST' and request.is_json:
            filter_data = request.json
            chain = filter_data.get('chain')
            project = filter_data.get('project')
            symbol = filter_data.get('symbol')
        else:
            chain = request.args.get('chain')
            project = request.args.get('project')
            symbol = request.args.get('symbol')
        
        # Prepare lists for filtering
        chains = [c.strip().lower() for c in chain.split(',')] if chain else []
        projects = [p.strip().lower() for p in project.split(',')] if project else []
        symbols = [s.strip().lower() for s in symbol.split(',')] if symbol else []
        
        # Get data from DefiLlama
        response = requests.get("https://yields.llama.fi/pools")
        response.raise_for_status()
        data = response.json()
        
        # Filter the data
        filtered_data = []
        
        for pool in data.get('data', []):
            # Only include pools with valid APY values
            apy = pool.get('apy')
            if apy is not None and apy > 0:
                # Apply other filters if provided
                if (not chains or pool.get('chain', '').lower() in chains) and \
                   (not projects or pool.get('project', '').lower() in projects) and \
                   (not symbols or pool.get('symbol', '').lower() in symbols):
                    filtered_data.append(pool)
        
        # Replace original data with filtered data
        data['data'] = filtered_data
        
        return jsonify(data)
    except requests.RequestException as e:
        return jsonify({'error': f'Error fetching data from DefiLlama: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/api/morpho-markets', methods=['GET'])
def get_morpho_markets():
    """
    Get Morpho markets data with optional filtering by loan asset address and multiple market names
    Example: /api/morpho-markets?loanAssetAddress=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48&first=15&orderBy=TotalLiquidityUsd&marketName=USDC,ETH,WBTC
    """
    try:
        loan_asset_address = request.args.get('loanAssetAddress')
        first = int(request.args.get('first', 100))
        order_by = request.args.get('orderBy')
        market_name = request.args.get('marketName')

        markets = asyncio.run(get_morpho_markets_data(
            first=first,
            loan_asset_address=loan_asset_address,
            order_by=order_by
        ))


        if market_name:
            names = [n.strip().lower() for n in market_name.split(',')]
            filtered = [m for m in markets if m.get('name', '').lower() in names]
            grouped = defaultdict(list)
            for m in filtered:
                grouped[m['name']].append(m)
            markets = [
                max(group, key=lambda x: x.get('total_supplied', 0))
                for group in grouped.values()
            ]

        return jsonify({
            "status": "success",
            "data": markets
        })
    except UnsupportedLoanAssetError as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Failed to fetch Morpho markets: {str(e)}"
        }), 500

@app.route('/api/calculate-apy', methods=['POST'])
def calculate_apy():
    """
    Calculates optimal allocation based on prepared data and also calculates
    APY for each market/reserve as if all funds were invested only in it
    """
    try:
        data = request.get_json()
        
        if not data or 'total_funds' not in data:
            return jsonify({'error': 'Missing total_funds parameter'}), 400
            
        total_funds = float(data['total_funds'])
        
        # Convert pools and reserves to investment data format
        investments_data = []
        
        # Process pools
        for pool in data.get('pools', []):
            pool_investment = {
                'type': 'pool',
                'name': pool['name'] + ' ' + pool['source'],
                'daily_fee': pool['daily_fee'],
                'pool_distribution': pool['pool_distribution'],
                'id': pool.get('id', '0x0000000000000000000000000000000000000000')
            }
            
            # Add reward info if available
            if 'reward_per_day' in pool and 'reward_token_price' in pool:
                pool_investment['reward_per_day'] = pool['reward_per_day']
                pool_investment['reward_token_price'] = pool['reward_token_price']
                
            investments_data.append(pool_investment)
            
        # Process reserves
        for reserve in data.get('reserves', []):
            reserve_investment = {
                'type': 'reserve',
                'name': reserve.get('name', 'Reserve') + ' ' + reserve.get('source', ''),
                'id': reserve.get('id', '0x0000000000000000000000000000000000000000')
            }
            for key, value in reserve.items():
                if key not in reserve_investment:
                    reserve_investment[key] = value
            investments_data.append(reserve_investment)
            
        if not investments_data:
            return jsonify({'error': 'No valid investment data provided'}), 400
            
        # Create parameters and calculate optimal distribution
        params = create_investment_parameters(investments_data)
        result = calculate_optimal_distribution(total_funds, params)
        
        # Ensure all details have ID field
        details = result.get('details', [])
        for i, allocation in enumerate(details):
            # Get ID from original investment data
            if i < len(investments_data):
                allocation['id'] = investments_data[i].get('id', '0x000')
            else:
                allocation['id'] = '0x0000000000000000000000000000000000000000'
        
        # Calculate separate details - APY for each investment if all funds were allocated to it
        separate_details = []
        
        if total_funds > 0:
            for i, investment in enumerate(investments_data):
                # Create parameters for single investment with all funds
                single_investment_params = create_investment_parameters([investment])
                single_result = calculate_optimal_distribution(total_funds, single_investment_params)
                
                if single_result.get('details') and len(single_result['details']) > 0:
                    single_detail = single_result['details'][0].copy()
                    # Remove allocated_amount from separate_details as it's not needed
                    single_detail.pop('allocated_amount', None)
                    single_detail.pop('percentage', None)
                    single_detail.pop('expected_profit', None)
                    single_detail['id'] = investment.get('id', '0x0000000000000000000000000000000000000000')
                    separate_details.append(single_detail)
                else:
                    # Fallback if calculation fails
                    fallback_detail = {
                        'base_apr': 0,
                        'base_apy': 0,
                        'id': investment.get('id', '0x0000000000000000000000000000000000000000'),
                        'name': investment.get('name', 'Unknown'),
                        'rewards_apr': 0,
                        'rewards_apy': 0,
                        'total_apr': 0,
                        'total_apy': 0,
                        'type': investment.get('type', 'unknown'),
                        'utilization_rate': 0
                    }
                    separate_details.append(fallback_detail)
        else:
            # If total_funds is 0, create separate details with 0 amounts but preserve APY calculations
            for i, investment in enumerate(investments_data):
                single_investment_params = create_investment_parameters([investment])
                single_result = calculate_optimal_distribution(1, single_investment_params)  # Use 1 to get APY calculation
                
                if single_result.get('details') and len(single_result['details']) > 0:
                    single_detail = single_result['details'][0].copy()
                    # Remove allocation fields from separate_details as they're not needed
                    single_detail.pop('allocated_amount', None)
                    single_detail.pop('percentage', None)
                    single_detail.pop('expected_profit', None)
                    single_detail['id'] = investment.get('id', '0x0000000000000000000000000000000000000000')
                    separate_details.append(single_detail)
                else:
                    # Fallback for zero funds case
                    fallback_detail = {
                        'base_apr': 0,
                        'base_apy': 0,
                        'id': investment.get('id', '0x0000000000000000000000000000000000000000'),
                        'name': investment.get('name', 'Unknown'),
                        'rewards_apr': 0,
                        'rewards_apy': 0,
                        'total_apr': 0,
                        'total_apy': 0,
                        'type': investment.get('type', 'unknown'),
                        'utilization_rate': 0
                    }
                    separate_details.append(fallback_detail)
        
        # If total_funds is 0, set all allocations to 0 but keep APY calculations
        if total_funds == 0:
            for allocation in details:
                allocation['allocated_amount'] = 0
                allocation['percentage'] = 0
                allocation['expected_profit'] = 0
            
            # Create result structure with desired order
            ordered_result = {
                'allocation_details': result['details'],
                'separate_details': separate_details
            }
            
            return jsonify(ordered_result)
        
        # Get minimum allocation percentage (default to 1%)
        min_allocation_percent = float(data.get('min_allocation_percent', 1.0))
        min_allocation_threshold = total_funds * (min_allocation_percent / 100.0)
        
        # If total_funds is too small to satisfy minimum allocation threshold,
        # allocate everything to the highest APY investment
        if total_funds < min_allocation_threshold * len(investments_data):
            if details:
                # Find investment with highest APY
                best_investment = max(details, key=lambda x: x.get('total_apy', 0))
                best_investment['allocated_amount'] = total_funds
                best_investment['percentage'] = 100
                best_investment['expected_profit'] = best_investment.get('total_apy', 0) * total_funds / 100
                
                # Set others to zero
                for allocation in details:
                    if allocation != best_investment:
                        allocation['allocated_amount'] = 0
                        allocation['percentage'] = 0
                        allocation['expected_profit'] = 0
                
                # Create result structure with desired order
                ordered_result = {
                    'allocation_details': result['details'],
                    'separate_details': separate_details
                }
                
                return jsonify(ordered_result)
        
        # Apply minimum allocation threshold
        # Step 1: Identify investments below threshold
        reallocate_amount = 0
        valid_allocations = []
        
        for allocation in details:
            if allocation['allocated_amount'] < min_allocation_threshold:
                # Add this amount to be reallocated
                reallocate_amount += allocation['allocated_amount']
                # Set to zero
                allocation['allocated_amount'] = 0
                allocation['percentage'] = 0
                if 'expected_profit' in allocation:
                    allocation['expected_profit'] = 0
            else:
                valid_allocations.append(allocation)
        
        # Step 2: Redistribute funds to remaining valid allocations
        if reallocate_amount > 0 and valid_allocations:
            # Calculate total valid allocation amount for proportional distribution
            total_valid_amount = sum(alloc['allocated_amount'] for alloc in valid_allocations)
            
            # Distribute reallocated funds proportionally
            for allocation in valid_allocations:
                proportion = allocation['allocated_amount'] / total_valid_amount
                additional_amount = reallocate_amount * proportion
                
                # Update allocation
                allocation['allocated_amount'] += additional_amount
                allocation['percentage'] = (allocation['allocated_amount'] / total_funds) * 100
                
                # Update profit if applicable
                if 'total_apy' in allocation:
                    allocation['expected_profit'] = allocation['total_apy'] * allocation['allocated_amount'] / 100
        
        # Create a new result structure with desired order
        ordered_result = {
            'allocation_details': result['details'],
            'separate_details': separate_details
        }
        
        return jsonify(ordered_result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# New endpoint for current APY (current_details)
@app.route('/api/current-apy', methods=['POST'])
def get_current_apy():
    """
    Returns current APY for each investment (with 0 our deposit)
    """
    try:
        data = request.get_json()
        if not data or ('pools' not in data and 'reserves' not in data):
            return jsonify({'error': 'No pools or reserves provided'}), 400
        investments_data = []
        for pool in data.get('pools', []):
            pool_investment = {
                'type': 'pool',
                'name': pool['name'] + ' ' + pool['source'],
                'daily_fee': pool['daily_fee'],
                'pool_distribution': pool['pool_distribution'],
                'id': pool.get('id', '0x0000000000000000000000000000000000000000')
            }
            if 'reward_per_day' in pool and 'reward_token_price' in pool:
                pool_investment['reward_per_day'] = pool['reward_per_day']
                pool_investment['reward_token_price'] = pool['reward_token_price']
            investments_data.append(pool_investment)
        for reserve in data.get('reserves', []):
            reserve_investment = {
                'type': 'reserve',
                'name': reserve.get('name', 'Reserve') + ' ' + reserve.get('source', ''),
                'id': reserve.get('id', '0x0000000000000000000000000000000000000000')
            }
            for key, value in reserve.items():
                if key not in reserve_investment:
                    reserve_investment[key] = value
            investments_data.append(reserve_investment)
        if not investments_data:
            return jsonify({'error': 'No valid investment data provided'}), 400
        current_details = []
        for i, investment in enumerate(investments_data):
            current_investment_params = create_investment_parameters([investment])
            current_result = calculate_optimal_distribution(0.01, current_investment_params)
            if current_result.get('details') and len(current_result['details']) > 0:
                current_detail = current_result['details'][0].copy()
                current_detail.pop('allocated_amount', None)
                current_detail.pop('percentage', None)
                current_detail.pop('expected_profit', None)
                current_detail['id'] = investment.get('id', '0x0000000000000000000000000000000000000000')
                current_details.append(current_detail)
            else:
                fallback_detail = {
                    'base_apr': 0,
                    'base_apy': 0,
                    'id': investment.get('id', '0x0000000000000000000000000000000000000000'),
                    'name': investment.get('name', 'Unknown'),
                    'rewards_apr': 0,
                    'rewards_apy': 0,
                    'total_apr': 0,
                    'total_apy': 0,
                    'type': investment.get('type', 'unknown'),
                    'utilization_rate': 0
                }
                current_details.append(fallback_detail)
        return jsonify({'details': current_details})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=8080) 
