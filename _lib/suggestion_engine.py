"""
Trade Suggestion Engine
Provides hold/buy/sell/exit recommendations based on:
- Greeks (Delta, Gamma, Theta, Vega)
- Market conditions (volume, OI, IV)
- Price action
- Time to expiry
- Risk/reward profile
"""
import sqlite3
from datetime import datetime
import yfinance as yf

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"

def get_greek_signal(delta, gamma, theta, vega, option_type):
    """
    Analyze Greeks to determine directional bias and position quality
    
    Returns: Signal strength (-1.0 to 1.0) and reasoning
    """
    signals = []
    reasons = []
    
    # Delta analysis (directional bias)
    if option_type == 'CALL':
        if delta > 0.75:
            signals.append(0.8)
            reasons.append("Deep ITM call (high delta) - directional bet")
        elif delta > 0.5:
            signals.append(0.5)
            reasons.append("ATM/ITM call - moderate bullish")
        elif delta > 0.25:
            signals.append(0.2)
            reasons.append("OTM call - speculative bullish")
        else:
            signals.append(-0.2)
            reasons.append("Far OTM call - unlikely to profit")
    else:  # PUT
        if delta < -0.75:
            signals.append(-0.8)
            reasons.append("Deep ITM put (high delta) - directional bet")
        elif delta < -0.5:
            signals.append(-0.5)
            reasons.append("ATM/ITM put - moderate bearish")
        elif delta < -0.25:
            signals.append(-0.2)
            reasons.append("OTM put - speculative bearish")
        else:
            signals.append(0.2)
            reasons.append("Far OTM put - unlikely to profit")
    
    # Gamma analysis (convexity/acceleration)
    if abs(gamma) > 0.005:
        signals.append(0.3)
        reasons.append("High gamma - strong acceleration expected")
    elif abs(gamma) < 0.001:
        signals.append(-0.2)
        reasons.append("Low gamma - minimal acceleration")
    else:
        signals.append(0.1)
        reasons.append("Moderate gamma - normal acceleration")
    
    # Theta analysis (time decay)
    if theta < -0.05:
        signals.append(-0.4)
        reasons.append("High negative theta - rapid decay")
    elif theta < -0.02:
        signals.append(-0.2)
        reasons.append("Moderate theta decay")
    elif theta > 0.02:
        signals.append(0.3)
        reasons.append("Positive theta - benefits from time decay")
    else:
        signals.append(0)
        reasons.append("Minimal theta impact")
    
    # Vega analysis (IV sensitivity)
    if abs(vega) > 0.1:
        signals.append(0.2)
        reasons.append("High vega sensitivity to IV changes")
    else:
        signals.append(0)
        reasons.append("Low vega impact")
    
    avg_signal = sum(signals) / len(signals) if signals else 0
    return {
        'signal_strength': avg_signal,
        'signal_direction': 'bullish' if avg_signal > 0 else 'bearish' if avg_signal < 0 else 'neutral',
        'greek_reasons': reasons,
        'greek_scores': {
            'delta': signals[0] if len(signals) > 0 else 0,
            'gamma': signals[1] if len(signals) > 1 else 0,
            'theta': signals[2] if len(signals) > 2 else 0,
            'vega': signals[3] if len(signals) > 3 else 0
        }
    }


def analyze_market_conditions(ticker, option_type=None, strike=None, expiry=None):
    """
    Analyze current market conditions (volume, OI, IV)
    
    Returns: Market strength signal and liquidity assessment
    """
    try:
        stock = yf.Ticker(ticker)
        
        # Get current price
        current_data = stock.history(period='1d')
        if current_data.empty:
            return {'market_signal': 0, 'liquidity': 'unknown', 'reason': 'No data available'}
        
        current_price = current_data['Close'].iloc[-1]
        
        # Get options chain
        try:
            options = stock.option_chain(expiry)
            
            # Volume analysis
            if option_type == 'CALL':
                options_df = options.calls
            else:
                options_df = options.puts
            
            total_volume = options_df['volume'].sum()
            avg_volume = options_df['volume'].mean()
            liquidity = 'high' if avg_volume > 1000 else 'medium' if avg_volume > 100 else 'low'
            
            # OI concentration
            total_oi = options_df['openInterest'].sum()
            if total_oi > 100000:
                oi_signal = 0.5
                oi_reason = "High open interest - liquid market"
            elif total_oi > 10000:
                oi_signal = 0.2
                oi_reason = "Moderate OI - acceptable liquidity"
            else:
                oi_signal = -0.3
                oi_reason = "Low OI - illiquid, wider spreads expected"
            
            return {
                'market_signal': oi_signal,
                'liquidity': liquidity,
                'total_volume': int(total_volume),
                'avg_volume': int(avg_volume),
                'total_oi': int(total_oi),
                'reason': oi_reason
            }
        except:
            return {'market_signal': 0, 'liquidity': 'unknown', 'reason': 'Options chain unavailable'}
    
    except Exception as e:
        return {'market_signal': 0, 'liquidity': 'unknown', 'reason': f'Error: {str(e)}'}


def analyze_technical_levels(ticker, entry_price):
    """
    Analyze price action vs entry level
    
    Returns: Technical signal based on support/resistance
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period='3mo')
        
        if hist.empty:
            return {'technical_signal': 0, 'level_type': 'unknown'}
        
        current_price = hist['Close'].iloc[-1]
        high_52w = hist['High'].max()
        low_52w = hist['Low'].min()
        resistance = hist['Close'].tail(20).max()
        support = hist['Close'].tail(20).min()
        
        price_from_entry_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
        
        # Classify location relative to levels
        if current_price > resistance:
            level_type = 'above_resistance'
            signal = -0.2
            reason = "Price above resistance - potential pullback"
        elif current_price < support:
            level_type = 'below_support'
            signal = 0.3
            reason = "Price below support - potential bounce"
        elif current_price >= entry_price * 0.95:
            level_type = 'at_entry'
            signal = 0
            reason = "Price near entry level"
        else:
            level_type = 'near_entry'
            signal = -0.1
            reason = "Price below entry - losses accumulating"
        
        return {
            'technical_signal': signal,
            'current_price': current_price,
            'entry_price': entry_price,
            'price_change_pct': price_from_entry_pct,
            'level_type': level_type,
            '52w_high': high_52w,
            '52w_low': low_52w,
            'support': support,
            'resistance': resistance,
            'reason': reason
        }
    except Exception as e:
        return {'technical_signal': 0, 'level_type': 'unknown', 'reason': f'Error: {str(e)}'}


def calculate_time_decay_impact(days_to_expiry, theta):
    """
    Assess impact of time decay on position
    
    Returns: Time decay urgency signal
    """
    if days_to_expiry <= 0:
        return {'urgency': 'CRITICAL', 'action': 'EXIT - EXPIRED', 'days_left': 0}
    elif days_to_expiry <= 3:
        return {
            'urgency': 'CRITICAL',
            'action': 'EXIT or ROLL',
            'days_left': days_to_expiry,
            'reason': 'Expiration imminent - high time decay acceleration'
        }
    elif days_to_expiry <= 7:
        return {
            'urgency': 'HIGH',
            'action': 'Consider exit',
            'days_left': days_to_expiry,
            'reason': 'One week to expiry - accelerating time decay'
        }
    elif days_to_expiry <= 30:
        return {
            'urgency': 'MEDIUM',
            'action': 'Monitor theta impact',
            'days_left': days_to_expiry,
            'reason': 'Monthly expiry - moderate time decay'
        }
    else:
        return {
            'urgency': 'LOW',
            'action': 'Theta manageable',
            'days_left': days_to_expiry,
            'reason': 'Sufficient time - theta decay gradual'
        }


def generate_trade_suggestion(trade_id=None, ticker=None, entry_price=None, delta=None, 
                            gamma=None, theta=None, vega=None, option_type='CALL',
                            strike=None, expiry=None, days_to_expiry=None, current_price=None, pnl_pct=None):
    """
    Generate comprehensive trade suggestion: HOLD / EXIT / BUY / SELL
    
    Returns: Detailed suggestion with reasoning and confidence score
    """
    
    # 1. Greek Signal Analysis
    greek_analysis = get_greek_signal(delta, gamma, theta, vega, option_type)
    
    # 2. Market Conditions Analysis
    market_analysis = analyze_market_conditions(ticker, option_type, strike, expiry)
    
    # 3. Technical Level Analysis
    technical_analysis = analyze_technical_levels(ticker, entry_price)
    
    # 4. Time Decay Analysis
    if not days_to_expiry and expiry:
        from datetime import datetime as dt
        exp_date = dt.strptime(expiry, '%Y-%m-%d')
        days_to_expiry = (exp_date - dt.now()).days
    
    time_analysis = calculate_time_decay_impact(days_to_expiry, theta) if days_to_expiry else {}
    
    # 5. Combine signals for recommendation
    signal_components = [
        greek_analysis['signal_strength'],
        market_analysis.get('market_signal', 0),
        technical_analysis.get('technical_signal', 0)
    ]
    
    overall_signal = sum(signal_components) / len(signal_components)
    confidence = min(abs(overall_signal) * 100, 95)  # Cap at 95%
    
    # Generate recommendation
    if days_to_expiry and days_to_expiry <= 3:
        recommendation = 'EXIT'
        action = 'Close position - expiration risk too high'
    elif pnl_pct and pnl_pct > 30:
        recommendation = 'EXIT'
        action = 'Take profits - 30%+ gain achieved'
    elif pnl_pct and pnl_pct < -50:
        recommendation = 'EXIT'
        action = 'Cut losses - 50%+ loss accumulated'
    elif overall_signal > 0.5:
        recommendation = 'BUY' if not trade_id else 'HOLD + ADD'
        action = 'Strong bullish signal - consider position increase'
    elif overall_signal > 0.2:
        recommendation = 'BUY' if not trade_id else 'HOLD'
        action = 'Positive signal - maintain or initiate position'
    elif overall_signal < -0.5:
        recommendation = 'SELL' if not trade_id else 'EXIT'
        action = 'Strong bearish signal - reduce exposure'
    elif overall_signal < -0.2:
        recommendation = 'SELL' if not trade_id else 'HOLD + MONITOR'
        action = 'Negative signal - consider reducing position'
    else:
        recommendation = 'HOLD'
        action = 'Neutral signal - maintain current position'
    
    return {
        'recommendation': recommendation,
        'action': action,
        'confidence_pct': confidence,
        'overall_signal': overall_signal,
        'greek_analysis': greek_analysis,
        'market_analysis': market_analysis,
        'technical_analysis': technical_analysis,
        'time_analysis': time_analysis,
        'analysis_timestamp': datetime.now().isoformat(),
        'reasoning': {
            'greek_reason': greek_analysis['greek_reasons'],
            'market_reason': market_analysis.get('reason', ''),
            'technical_reason': technical_analysis.get('reason', ''),
            'time_reason': time_analysis.get('reason', '')
        }
    }


def get_all_position_suggestions():
    """
    Get suggestions for all open positions
    
    Returns: DataFrame with trades and recommendations
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT 
            trade_id, ticker, option_type, entry_price, strike, expiry,
            current_delta, current_gamma, current_theta, current_vega,
            pnl_pct, days_held
        FROM trades 
        WHERE status = 'OPEN'
        """)
        
        columns = ['trade_id', 'ticker', 'option_type', 'entry_price', 'strike', 'expiry',
                  'delta', 'gamma', 'theta', 'vega', 'pnl_pct', 'days_held']
        
        trades = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        
        suggestions = []
        for trade in trades:
            try:
                from datetime import datetime as dt
                exp_date = dt.strptime(trade['expiry'], '%Y-%m-%d')
                days_to_expiry = (exp_date - dt.now()).days
                
                suggestion = generate_trade_suggestion(
                    trade_id=trade['trade_id'],
                    ticker=trade['ticker'],
                    entry_price=trade['entry_price'],
                    delta=trade['delta'],
                    gamma=trade['gamma'],
                    theta=trade['theta'],
                    vega=trade['vega'],
                    option_type=trade['option_type'],
                    strike=trade['strike'],
                    expiry=trade['expiry'],
                    days_to_expiry=days_to_expiry,
                    pnl_pct=trade['pnl_pct']
                )
                
                suggestion['trade_id'] = trade['trade_id']
                suggestion['ticker'] = trade['ticker']
                suggestion['pnl_pct'] = trade['pnl_pct']
                suggestion['days_to_expiry'] = days_to_expiry
                suggestions.append(suggestion)
            except Exception as e:
                print(f"Error generating suggestion for trade {trade['trade_id']}: {e}")
        
        return suggestions
    except Exception as e:
        print(f"Error getting position suggestions: {e}")
        return []


if __name__ == '__main__':
    print("Trade Suggestion Engine - Functions Available:")
    print("=" * 60)
    print()
    print("Key Functions:")
    print("  get_greek_signal(delta, gamma, theta, vega, option_type)")
    print("  analyze_market_conditions(ticker, option_type, strike, expiry)")
    print("  analyze_technical_levels(ticker, entry_price)")
    print("  calculate_time_decay_impact(days_to_expiry, theta)")
    print("  generate_trade_suggestion(...) - Full recommendation")
    print("  get_all_position_suggestions() - All open position recommendations")
    print()
