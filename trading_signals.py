import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
import logging
import requests
import threading
import asyncio
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import json
import os
from typing import Dict, List, Optional

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('crypto_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------- Configuration ----------
class Config:
    TELEGRAM_BOT_TOKEN = "8495540103:AAFY9aKRkhYi173j37Dc7q112A4J4ssuIFs"  # Add numbers at front
    ADMIN_USER_IDS = [6404071195]
    UPDATE_INTERVAL = 60
    SYMBOLS = [
        'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
        'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT'
    ]

# ---------- Enhanced Signal Generator ----------
class AdvancedCryptoSignalGenerator:
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 30000
        })
        self.volume_threshold = 1.2
        self.signals_history = []

    def fetch_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 200):
        """Get OHLCV data from Binance"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            return pd.DataFrame()

    def calculate_technical_indicators(self, df: pd.DataFrame):
        """Calculate multiple technical indicators"""
        if df.empty:
            return df
        
        # RSI with different periods
        df = self.calculate_rsi(df, period=14)
        df = self.calculate_rsi(df, period=21)
        
        # Moving Averages for trend
        df['ema_20'] = df['close'].ewm(span=20).mean()
        df['ema_50'] = df['close'].ewm(span=50).mean()
        df['sma_100'] = df['close'].rolling(100).mean()
        
        # Volume indicators
        df['volume_sma_20'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_sma_20']
        
        # Support and Resistance levels
        df['resistance'] = df['high'].rolling(20).max()
        df['support'] = df['low'].rolling(20).min()
        
        # ATR for volatility
        df = self.calculate_atr(df, period=14)
        
        # MACD for momentum
        df = self.calculate_macd(df)
        
        return df

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14):
        """Compute RSI manually"""
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / loss
        df[f'rsi_{period}'] = 100 - (100 / (1 + rs))
        return df

    def calculate_atr(self, df: pd.DataFrame, period: int = 14):
        """Calculate Average True Range"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        true_range = np.maximum(np.maximum(high_low, high_close), low_close)
        df['atr'] = true_range.rolling(period).mean()
        return df

    def calculate_macd(self, df: pd.DataFrame):
        """Calculate MACD"""
        ema_12 = df['close'].ewm(span=12).mean()
        ema_26 = df['close'].ewm(span=26).mean()
        df['macd'] = ema_12 - ema_26
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']
        return df

    def identify_trend(self, df: pd.DataFrame):
        """Identify market trend using multiple timeframes"""
        if len(df) < 100:
            return "NEUTRAL"
        
        price = df['close'].iloc[-1]
        ema_20 = df['ema_20'].iloc[-1]
        ema_50 = df['ema_50'].iloc[-1]
        sma_100 = df['sma_100'].iloc[-1]
        
        ema_bullish = ema_20 > ema_50 > sma_100
        ema_bearish = ema_20 < ema_50 < sma_100
        
        macd_trend = "NEUTRAL"
        if df['macd'].iloc[-1] > df['macd_signal'].iloc[-1] and df['macd_histogram'].iloc[-1] > 0:
            macd_trend = "BULLISH"
        elif df['macd'].iloc[-1] < df['macd_signal'].iloc[-1] and df['macd_histogram'].iloc[-1] < 0:
            macd_trend = "BEARISH"
        
        if ema_bullish and macd_trend == "BULLISH" and price > ema_20:
            return "BULLISH"
        elif ema_bearish and macd_trend == "BEARISH" and price < ema_20:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def check_liquidity_support(self, df: pd.DataFrame):
        """Check if there's sufficient liquidity/volume"""
        if len(df) < 20:
            return False
        
        volume_ratio = df['volume_ratio'].iloc[-1]
        recent_volume_spike = any(df['volume_ratio'].tail(3) > 1.5)
        
        return volume_ratio > self.volume_threshold or recent_volume_spike

    def calculate_dynamic_sl_tp(self, df: pd.DataFrame, signal_type: str, price: float):
        """Calculate dynamic stop loss and take profit based on ATR"""
        atr = df['atr'].iloc[-1]
        atr_multiplier = 1.5
        
        if signal_type == "BUY":
            stop_loss = price - (atr * atr_multiplier)
            take_profit_1 = price + (atr * 1.0)
            take_profit_2 = price + (atr * 2.0)
            risk_reward = (take_profit_1 - price) / (price - stop_loss)
        else:
            stop_loss = price + (atr * atr_multiplier)
            take_profit_1 = price - (atr * 1.0)
            take_profit_2 = price - (atr * 2.0)
            risk_reward = (price - take_profit_1) / (stop_loss - price)
        
        return {
            'stop_loss': stop_loss,
            'take_profit_1': take_profit_1,
            'take_profit_2': take_profit_2,
            'atr': atr,
            'risk_reward': risk_reward
        }

    def generate_enhanced_signal(self, df: pd.DataFrame, symbol: str):
        """Generate signal with multiple confirmations"""
        if df.empty or len(df) < 100:
            return None

        latest = df.iloc[-1]
        price = latest['close']
        
        rsi_14 = latest['rsi_14']
        rsi_21 = latest['rsi_21']
        
        trend = self.identify_trend(df)
        has_liquidity = self.check_liquidity_support(df)
        
        rsi_oversold = rsi_14 < 30 and rsi_21 < 35
        rsi_overbought = rsi_14 > 70 and rsi_21 > 65
        
        signal = None
        reason_parts = []
        
        if trend == "BULLISH" and rsi_oversold and has_liquidity:
            signal = "BUY"
            reason_parts.append(f"Bullish trend + RSI oversold (14: {rsi_14:.1f}, 21: {rsi_21:.1f})")
        elif trend == "BEARISH" and rsi_overbought and has_liquidity:
            signal = "SELL"
            reason_parts.append(f"Bearish trend + RSI overbought (14: {rsi_14:.1f}, 21: {rsi_21:.1f})")
        
        if signal and self.check_rsi_confirmation(df, signal):
            sl_tp_data = self.calculate_dynamic_sl_tp(df, signal, price)
            confidence = self.calculate_confidence(df, signal)
            
            signal_data = {
                "symbol": symbol,
                "signal": signal,
                "price": float(price),
                "rsi_14": float(rsi_14),
                "rsi_21": float(rsi_21),
                "trend": trend,
                "stop_loss": sl_tp_data['stop_loss'],
                "take_profit_1": sl_tp_data['take_profit_1'],
                "take_profit_2": sl_tp_data['take_profit_2'],
                "atr": sl_tp_data['atr'],
                "risk_reward": sl_tp_data['risk_reward'],
                "volume_ratio": float(latest['volume_ratio']),
                "reason": " | ".join(reason_parts),
                "confidence": confidence,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Avoid duplicate signals
            if not self.is_duplicate_signal(signal_data):
                self.signals_history.append(signal_data)
                return signal_data
        
        return None

    def check_rsi_confirmation(self, df: pd.DataFrame, signal: str):
        """Check if RSI is confirming the signal"""
        if len(df) < 3:
            return False
        
        current_rsi = df['rsi_14'].iloc[-1]
        prev_rsi = df['rsi_14'].iloc[-2]
        
        if signal == "BUY":
            return current_rsi > prev_rsi
        else:
            return current_rsi < prev_rsi

    def calculate_confidence(self, df: pd.DataFrame, signal: str):
        """Calculate signal confidence score (0-100)"""
        confidence = 50
        
        volume_ratio = df['volume_ratio'].iloc[-1]
        if volume_ratio > 2.0:
            confidence += 20
        elif volume_ratio > 1.5:
            confidence += 10
        
        ema_20 = df['ema_20'].iloc[-1]
        ema_50 = df['ema_50'].iloc[-1]
        trend_strength = abs((ema_20 - ema_50) / df['close'].iloc[-1] * 100)
        
        if trend_strength > 2.0:
            confidence += 15
        elif trend_strength > 1.0:
            confidence += 8
        
        rsi_trend = "RISING" if df['rsi_14'].iloc[-1] > df['rsi_14'].iloc[-2] else "FALLING"
        
        if (signal == "BUY" and rsi_trend == "RISING") or (signal == "SELL" and rsi_trend == "FALLING"):
            confidence += 15
        
        return min(confidence, 100)

    def is_duplicate_signal(self, new_signal: Dict, time_window_minutes: int = 120):
        """Check if similar signal was generated recently"""
        current_time = datetime.now()
        
        for signal in reversed(self.signals_history):
            signal_time = datetime.strptime(signal['timestamp'], "%Y-%m-%d %H:%M:%S")
            time_diff = (current_time - signal_time).total_seconds() / 60
            
            if time_diff > time_window_minutes:
                break
                
            if (signal['symbol'] == new_signal['symbol'] and 
                signal['signal'] == new_signal['signal'] and
                abs(signal['price'] - new_signal['price']) / new_signal['price'] < 0.02):  # 2% price difference
                return True
        
        return False

    def get_market_overview(self):
        """Get overview of all monitored symbols"""
        overview = []
        for symbol in Config.SYMBOLS:
            try:
                df = self.fetch_ohlcv(symbol, '15m', 100)
                if not df.empty:
                    df = self.calculate_technical_indicators(df)
                    latest = df.iloc[-1]
                    
                    overview.append({
                        'symbol': symbol,
                        'price': latest['close'],
                        'rsi_14': latest['rsi_14'],
                        'rsi_21': latest['rsi_21'],
                        'trend': self.identify_trend(df),
                        'volume_ratio': latest['volume_ratio']
                    })
                
                time.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                logger.error(f"Error getting overview for {symbol}: {e}")
        
        return overview

# ---------- Telegram Bot ----------
class CryptoTelegramBot:
    def __init__(self):
        self.signal_generator = AdvancedCryptoSignalGenerator()
        self.application = None
        self.is_monitoring = False
        self.monitoring_thread = None

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_id = update.effective_user.id
        
        if user_id not in Config.ADMIN_USER_IDS:
            await update.message.reply_text("❌ Unauthorized access.")
            return
        
        welcome_text = """
🤖 *Crypto Signal Bot Started!*

Available commands:
/start - Start the bot
/status - Check bot status  
/overview - Market overview
/scan - Manual scan for signals
/signals - Recent signals
/stop - Stop monitoring
/help - Show this help

Bot will automatically monitor and alert for trading signals.
        """
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
        
        # Start monitoring if not already running
        if not self.is_monitoring:
            self.start_monitoring()

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        status_text = f"""
📊 *Bot Status*

✅ Monitoring: {self.is_monitoring}
📈 Symbols: {len(Config.SYMBOLS)}
🕒 Update Interval: {Config.UPDATE_INTERVAL}s
📋 Recent Signals: {len(self.signal_generator.signals_history)}
        """
        await update.message.reply_text(status_text, parse_mode='Markdown')

    async def overview_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /overview command"""
        await update.message.reply_text("🔄 Fetching market overview...")
        
        overview = self.signal_generator.get_market_overview()
        
        if not overview:
            await update.message.reply_text("❌ Failed to fetch market data")
            return
        
        overview_text = "📊 *Market Overview*\n\n"
        for item in overview:
            emoji = "🟢" if item['trend'] == "BULLISH" else "🔴" if item['trend'] == "BEARISH" else "🟡"
            overview_text += f"""
{emoji} *{item['symbol']}*
💰 Price: ${item['price']:.4f}
📊 RSI 14: {item['rsi_14']:.1f} | RSI 21: {item['rsi_21']:.1f}
🎯 Trend: {item['trend']}
📈 Volume: {item['volume_ratio']:.2f}x
────────────────
            """
        
        await update.message.reply_text(overview_text, parse_mode='Markdown')

    async def scan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /scan command - manual scan"""
        await update.message.reply_text("🔍 Scanning for signals...")
        
        signals_found = []
        for symbol in Config.SYMBOLS:
            try:
                df = self.signal_generator.fetch_ohlcv(symbol, '15m', 200)
                df = self.signal_generator.calculate_technical_indicators(df)
                signal = self.signal_generator.generate_enhanced_signal(df, symbol)
                
                if signal:
                    signals_found.append(signal)
                
                time.sleep(1)  # Rate limiting
                
            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")
        
        if signals_found:
            for signal in signals_found:
                await self.send_signal_alert(signal)
        else:
            await update.message.reply_text("❌ No signals found in manual scan")

    async def signals_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /signals command - show recent signals"""
        recent_signals = self.signal_generator.signals_history[-10:]  # Last 10 signals
        
        if not recent_signals:
            await update.message.reply_text("📭 No recent signals")
            return
        
        signals_text = "📋 *Recent Signals*\n\n"
        for signal in reversed(recent_signals):
            emoji = "🟢" if signal['signal'] == "BUY" else "🔴"
            signals_text += f"""
{emoji} *{signal['symbol']} - {signal['signal']}*
💰 Price: ${signal['price']:.4f}
📊 RSI: {signal['rsi_14']:.1f} | Confidence: {signal['confidence']}%
🎯 Trend: {signal['trend']}
🛑 SL: ${signal['stop_loss']:.4f}
🎯 TP1: ${signal['take_profit_1']:.4f}
🎯 TP2: ${signal['take_profit_2']:.4f}
⚖️ R/R: {signal['risk_reward']:.2f}:1
🕒 Time: {signal['timestamp']}
────────────────
            """
        
        await update.message.reply_text(signals_text, parse_mode='Markdown')

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop command"""
        self.is_monitoring = False
        await update.message.reply_text("🛑 Monitoring stopped")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = """
🤖 *Crypto Signal Bot Help*

*Commands:*
/start - Start the bot and monitoring
/status - Check bot status and statistics
/overview - Get market overview of all symbols
/scan - Manual scan for immediate signals
/signals - Show recent trading signals
/stop - Stop automatic monitoring
/help - Show this help message

*Features:*
• RSI-based signals with multiple confirmations
• Trend analysis and volume confirmation
• Dynamic stop loss and take profit levels
• Risk/reward ratio calculations
• Confidence scoring for each signal
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def send_signal_alert(self, signal: Dict):
        """Send signal alert to all authorized users"""
        emoji = "🟢" if signal['signal'] == "BUY" else "🔴"
        
        alert_text = f"""
{emoji} *TRADING SIGNAL ALERT* {emoji}

*{signal['symbol']} - {signal['signal']} SIGNAL*

💰 *Price:* ${signal['price']:.4f}
📊 *RSI 14:* {signal['rsi_14']:.1f} | *RSI 21:* {signal['rsi_21']:.1f}
🎯 *Trend:* {signal['trend']}
📈 *Volume Ratio:* {signal['volume_ratio']:.2f}x
✅ *Confidence:* {signal['confidence']}%

🛑 *Stop Loss:* ${signal['stop_loss']:.4f}
🎯 *Take Profit 1:* ${signal['take_profit_1']:.4f}
🎯 *Take Profit 2:* ${signal['take_profit_2']:.4f}
⚖️ *Risk/Reward:* {signal['risk_reward']:.2f}:1

📝 *Reason:* {signal['reason']}
🕒 *Time:* {signal['timestamp']}
        """
        
        for user_id in Config.ADMIN_USER_IDS:
            try:
                await self.application.bot.send_message(
                    chat_id=user_id,
                    text=alert_text,
                    parse_mode='Markdown'
                )
                logger.info(f"Signal alert sent to user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send alert to user {user_id}: {e}")

    def monitoring_loop(self):
        """Main monitoring loop"""
        cycle_count = 0
        while self.is_monitoring:
            try:
                cycle_count += 1
                logger.info(f"Monitoring cycle {cycle_count} started")
                
                for symbol in Config.SYMBOLS:
                    if not self.is_monitoring:
                        break
                        
                    try:
                        df = self.signal_generator.fetch_ohlcv(symbol, '15m', 200)
                        if df.empty:
                            continue
                            
                        df = self.signal_generator.calculate_technical_indicators(df)
                        signal = self.signal_generator.generate_enhanced_signal(df, symbol)
                        
                        if signal:
                            # Use asyncio to send alert from thread
                            asyncio.run_coroutine_threadsafe(
                                self.send_signal_alert(signal), 
                                self.application.loop
                            )
                            logger.info(f"Signal generated for {symbol}: {signal['signal']}")
                        
                        time.sleep(2)  # Rate limiting between symbols
                        
                    except Exception as e:
                        logger.error(f"Error processing {symbol}: {e}")
                        continue
                
                logger.info(f"Cycle {cycle_count} completed. Waiting {Config.UPDATE_INTERVAL} seconds...")
                time.sleep(Config.UPDATE_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(60)

    def start_monitoring(self):
        """Start the monitoring thread"""
        if not self.is_monitoring:
            self.is_monitoring = True
            self.monitoring_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
            self.monitoring_thread.start()
            logger.info("Monitoring started")

    def run(self):
        """Start the Telegram bot"""
        # Create application
        self.application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        
        # Add handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("overview", self.overview_command))
        self.application.add_handler(CommandHandler("scan", self.scan_command))
        self.application.add_handler(CommandHandler("signals", self.signals_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        
        # Start monitoring
        self.start_monitoring()
        
        # Start the bot
        logger.info("Telegram bot starting...")
        self.application.run_polling()

# ---------- Run ----------
if __name__ == "__main__":
    # Quick validation
    if len(Config.TELEGRAM_BOT_TOKEN) < 10:
        print("❌ Invalid Telegram Bot Token")
        print(f"Current token: {Config.TELEGRAM_BOT_TOKEN}")
        print("💡 Get your complete token from @BotFather")
        exit(1)
    
    print("🤖 Starting Crypto Signal Telegram Bot...")
    print(f"📱 Monitoring {len(Config.SYMBOLS)} symbols")
    print(f"🆔 Admin User ID: {Config.ADMIN_USER_IDS[0]}")
    print("💡 Go to Telegram and send /start to your bot")
    
    bot = CryptoTelegramBot()
    bot.run()