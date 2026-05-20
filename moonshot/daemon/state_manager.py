"""
State Manager for Moonshot Daemon
Handles position tracking, trade history, and state persistence
"""

import json
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, date
from collections import deque
import threading
import os

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    """Current state of an open position"""
    symbol: str
    side: str  # "long" or "short"
    size: float
    entry_price: float
    current_price: float
    leverage: float
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    margin_used: float = 0.0
    liquidation_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    open_time: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'symbol': self.symbol,
            'side': self.side,
            'size': self.size,
            'entry_price': self.entry_price,
            'current_price': self.current_price,
            'leverage': self.leverage,
            'unrealized_pnl': self.unrealized_pnl,
            'unrealized_pnl_pct': self.unrealized_pnl_pct,
            'margin_used': self.margin_used,
            'liquidation_price': self.liquidation_price,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'open_time': self.open_time,
            'last_update': self.last_update,
            'open_datetime': datetime.fromtimestamp(self.open_time).isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PositionState':
        """Create from dictionary"""
        return cls(
            symbol=data['symbol'],
            side=data['side'],
            size=data['size'],
            entry_price=data['entry_price'],
            current_price=data.get('current_price', data['entry_price']),
            leverage=data['leverage'],
            unrealized_pnl=data.get('unrealized_pnl', 0.0),
            unrealized_pnl_pct=data.get('unrealized_pnl_pct', 0.0),
            margin_used=data.get('margin_used', 0.0),
            liquidation_price=data.get('liquidation_price'),
            stop_loss=data.get('stop_loss'),
            take_profit=data.get('take_profit'),
            open_time=data['open_time'],
            last_update=data.get('last_update', data['open_time'])
        )


@dataclass
class TradeRecord:
    """Record of a completed trade"""
    trade_id: str
    symbol: str
    side: str  # "long" or "short"
    entry_time: float
    exit_time: float
    entry_price: float
    exit_price: float
    size: float
    leverage: float
    pnl: float
    pnl_pct: float
    exit_reason: str  # "tp", "sl", "signal", "manual"
    fees: float = 0.0
    strategy: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            'trade_id': self.trade_id,
            'symbol': self.symbol,
            'side': self.side,
            'entry_time': self.entry_time,
            'exit_time': self.exit_time,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'size': self.size,
            'leverage': self.leverage,
            'pnl': self.pnl,
            'pnl_pct': self.pnl_pct,
            'exit_reason': self.exit_reason,
            'fees': self.fees,
            'strategy': self.strategy,
            'entry_datetime': datetime.fromtimestamp(self.entry_time).isoformat(),
            'exit_datetime': datetime.fromtimestamp(self.exit_time).isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TradeRecord':
        return cls(
            trade_id=data['trade_id'],
            symbol=data['symbol'],
            side=data['side'],
            entry_time=float(data['entry_time']),
            exit_time=float(data['exit_time']),
            entry_price=float(data['entry_price']),
            exit_price=float(data['exit_price']),
            size=float(data['size']),
            leverage=float(data['leverage']),
            pnl=float(data['pnl']),
            pnl_pct=float(data.get('pnl_pct', 0.0)),
            exit_reason=data.get('exit_reason', ''),
            fees=float(data.get('fees', 0.0)),
            strategy=data.get('strategy', ''),
        )


class StateManager:
    """
    Manages all state for the trading bot
    - Tracks open positions
    - Records trade history
    - Persists state to disk
    - Handles crash recovery
    """
    
    def __init__(
        self,
        data_dir: str = "state/moonshot",
        save_interval: float = 30.0,
        max_trade_history: int = 10000
    ):
        self.data_dir = data_dir
        self.save_interval = save_interval
        self.max_trade_history = max_trade_history
        
        # Ensure data directory exists
        os.makedirs(data_dir, exist_ok=True)
        
        # State storage
        self.positions: Dict[str, PositionState] = {}
        self.trade_history: deque = deque(maxlen=max_trade_history)
        self.pending_orders: Dict[str, Any] = {}
        
        # Account state
        self.balance: float = 100.0  # Starting balance
        self.equity: float = 100.0
        self.margin_used: float = 0.0
        self.free_margin: float = 100.0
        
        # Statistics
        self.daily_stats = {
            'date': datetime.now().date().isoformat(),
            'starting_equity': 100.0,
            'trades': 0,
            'wins': 0,
            'losses': 0,
            'pnl': 0.0
        }
        
        # File paths
        self.positions_file = os.path.join(data_dir, "positions.json")
        self.state_file = os.path.join(data_dir, "state.json")
        self.trades_file = os.path.join(data_dir, "trades.jsonl")
        
        # Threading
        self._lock = threading.RLock()
        self._save_thread: Optional[threading.Thread] = None
        self._running = False
        
        # Load existing state
        self._load_state()
        
        logger.info(f"StateManager initialized")
        logger.info(f"  Data directory: {data_dir}")
        logger.info(f"  Save interval: {save_interval}s")
        logger.info(f"  Positions loaded: {len(self.positions)}")
        logger.info(f"  Balance: {self.balance:.2f} USDT")
    
    def start_auto_save(self):
        """Start automatic state saving"""
        self._running = True
        self._save_thread = threading.Thread(target=self._auto_save_loop, daemon=True)
        self._save_thread.start()
        logger.info("Auto-save started")
    
    def stop_auto_save(self):
        """Stop automatic state saving"""
        self._running = False
        if self._save_thread:
            self._save_thread.join(timeout=5)
        # Final save
        self.save_state()
        logger.info("Auto-save stopped")
    
    def _auto_save_loop(self):
        """Auto-save loop"""
        while self._running:
            time.sleep(self.save_interval)
            if self._running:
                try:
                    self.save_state()
                except Exception as e:
                    logger.error(f"Auto-save error: {e}")
    
    def save_state(self):
        """Save current state to disk"""
        with self._lock:
            try:
                # Save positions
                positions_data = {
                    symbol: pos.to_dict()
                    for symbol, pos in self.positions.items()
                }
                
                with open(self.positions_file, 'w') as f:
                    json.dump(positions_data, f, indent=2)
                
                # Save state
                # Convert daily_stats date to string for JSON serialization
                state_data = {
                    'balance': self.balance,
                    'equity': self.equity,
                    'margin_used': self.margin_used,
                    'free_margin': self.free_margin,
                    'daily_stats': self.daily_stats,
                    'timestamp': time.time(),
                    'datetime': datetime.now().isoformat()
                }
                
                with open(self.state_file, 'w') as f:
                    json.dump(state_data, f, indent=2)
                
                logger.debug(f"State saved: {len(self.positions)} positions, balance={self.balance:.2f}")
                
            except Exception as e:
                logger.error(f"Error saving state: {e}")
                raise
    
    def _load_state(self):
        """Load state from disk"""
        try:
            if os.path.exists(self.positions_file):
                with open(self.positions_file, 'r') as f:
                    content = f.read().strip()
                    if content and content != '{}':
                        positions_data = json.loads(content)
                        for symbol, data in positions_data.items():
                            try:
                                self.positions[symbol] = PositionState.from_dict(data)
                            except Exception as e:
                                logger.error(f"Error loading position {symbol}: {e}")
                        logger.info(f"Loaded {len(self.positions)} positions from disk")
                    else:
                        logger.info("No positions to load")

            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    content = f.read().strip()
                    if not content:
                        logger.info("Empty state file, using defaults")
                    else:
                        state_data = json.loads(content)
                        self.balance = state_data.get('balance', 100.0)
                        self.equity = state_data.get('equity', 100.0)
                        self.margin_used = state_data.get('margin_used', 0.0)
                        self.free_margin = state_data.get('free_margin', 100.0)

                        loaded_stats = state_data.get('daily_stats', {})
                        if loaded_stats:
                            date_val = loaded_stats.get('date')
                            if isinstance(date_val, date) and not isinstance(date_val, datetime):
                                loaded_stats['date'] = date_val.isoformat()
                            self.daily_stats.update(loaded_stats)

                        logger.info(f"Loaded state: balance={self.balance:.2f}, equity={self.equity:.2f}")

            if os.path.exists(self.trades_file):
                with open(self.trades_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self.trade_history.append(TradeRecord.from_dict(json.loads(line)))
                        except Exception as e:
                            logger.debug(f"Skipping bad trade history row: {e}")
                logger.info(f"Loaded {len(self.trade_history)} historical trades from disk")

        except json.JSONDecodeError as e:
            logger.error(f"Corrupt state file, resetting to defaults: {e}")
            self._reset_state_file()
        except Exception as e:
            logger.error(f"Error loading state: {e}")

    def _reset_state_file(self):
        """Reset state files to clean defaults"""
        for fpath in [self.positions_file, self.state_file]:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except Exception:
                pass
    
    def add_position(self, position: PositionState):
        """Add a new position"""
        with self._lock:
            self.positions[position.symbol] = position
            logger.info(f"Position added: {position.symbol} {position.side} {position.size} @ {position.entry_price}")
    
    def remove_position(self, symbol: str) -> Optional[PositionState]:
        """Remove a position"""
        with self._lock:
            position = self.positions.pop(symbol, None)
            if position:
                logger.info(f"Position removed: {symbol}")
            return position
    
    def update_position_price(self, symbol: str, current_price: float):
        """Update position with current market price"""
        with self._lock:
            if symbol in self.positions:
                position = self.positions[symbol]
                position.current_price = current_price
                
                # Calculate unrealized PnL
                if position.side == "long":
                    position.unrealized_pnl = (current_price - position.entry_price) * position.size
                else:  # short
                    position.unrealized_pnl = (position.entry_price - current_price) * position.size
                
                position.unrealized_pnl_pct = (position.unrealized_pnl / (position.entry_price * position.size)) * 100
                position.last_update = time.time()
    
    def add_trade(self, trade: TradeRecord):
        """Add a completed trade to history"""
        with self._lock:
            self.trade_history.append(trade)
            
            # Append to file
            try:
                with open(self.trades_file, 'a') as f:
                    f.write(json.dumps(trade.to_dict()) + '\n')
            except Exception as e:
                logger.error(f"Error writing trade to file: {e}")
            
            # Update daily stats
            self.daily_stats['trades'] += 1
            if trade.pnl > 0:
                self.daily_stats['wins'] += 1
            else:
                self.daily_stats['losses'] += 1
            self.daily_stats['pnl'] += trade.pnl
            
            logger.info(f"Trade recorded: {trade.symbol} {trade.side} PnL={trade.pnl:.2f} ({trade.exit_reason})")
    
    def get_position(self, symbol: str) -> Optional[PositionState]:
        """Get a specific position"""
        return self.positions.get(symbol)
    
    def get_all_positions(self) -> Dict[str, PositionState]:
        """Get all positions"""
        return self.positions.copy()
    
    def get_trade_history(self, limit: int = 100) -> List[TradeRecord]:
        """Get recent trade history"""
        return list(self.trade_history)[-limit:]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        with self._lock:
            # Calculate PnL from positions
            unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())
            
            # Calculate margin usage
            total_margin = sum(p.margin_used for p in self.positions.values())
            
            return {
                'balance': self.balance,
                'equity': self.equity,
                'unrealized_pnl': unrealized_pnl,
                'total_pnl': self.equity - 100.0,  # Assuming 100 start
                'free_margin': self.free_margin,
                'margin_used': total_margin,
                'margin_level': (self.equity / total_margin * 100) if total_margin > 0 else 0,
                'open_positions': len(self.positions),
                'total_trades': len(self.trade_history),
                'daily_stats': self.daily_stats,
                'timestamp': time.time()
            }