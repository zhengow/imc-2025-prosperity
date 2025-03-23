from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict
from collections import deque
import json

class Trader:
    def __init__(self):
        # 为所有产品设置默认持仓限制
        self.default_position_limit = 20
        # 产品特定的持仓限制
        self.position_limits = {}
        # 为每个产品维护一个单独的滑动窗口，记录在字典中
        self.windows = {}
    
    def run(self, state: TradingState):
        # 初始化结果字典和转换数量
        result = {}
        conversions = 0
        
        # 恢复之前的状态（如果有）
        if state.traderData != "":
            try:
                trader_data = json.loads(state.traderData)
                if "windows" in trader_data:
                    # 恢复每个产品的窗口状态
                    for symbol, window_data in trader_data["windows"].items():
                        self.windows[symbol] = deque(window_data, maxlen=10)
                if "position_limits" in trader_data:
                    self.position_limits = trader_data["position_limits"]
            except json.JSONDecodeError:
                pass
        
        # 对每个产品执行交易策略
        for product in state.order_depths:
            # 如果产品有足够的订单深度，应用市场做市商策略
            if len(state.order_depths[product].buy_orders) > 0 and len(state.order_depths[product].sell_orders) > 0:
                # 确保该产品有位置限制，如果没有则使用默认值
                if product not in self.position_limits:
                    self.position_limits[product] = self.default_position_limit
                
                # 确保该产品有滑动窗口，如果没有则创建一个
                if product not in self.windows:
                    self.windows[product] = deque(maxlen=10)
                
                # 使用市场做市商策略
                orders = self.market_making_strategy(product, state)
                result[product] = orders
            else:
                # 如果订单深度不足，返回空订单列表
                result[product] = []
        
        # 保存状态以便下次使用
        trader_data = {
            "windows": {symbol: list(window) for symbol, window in self.windows.items()},
            "position_limits": self.position_limits
        }
        
        return result, conversions, json.dumps(trader_data)
    
    def market_making_strategy(self, symbol: str, state: TradingState) -> List[Order]:
        """
        市场做市商策略
        基于市场上最受欢迎的买卖价格估计真实价值
        对所有产品通用
        """
        orders = []
        order_depth = state.order_depths[symbol]
        position_limit = self.position_limits[symbol]
        window = self.windows[symbol]
        
        # 排序买卖订单
        buy_orders = sorted(order_depth.buy_orders.items(), reverse=True)
        sell_orders = sorted(order_depth.sell_orders.items())
        
        # 计算"真实价值"：最受欢迎买卖价格的平均值
        popular_buy_price = max(buy_orders, key=lambda tup: tup[1])[0]
        popular_sell_price = min(sell_orders, key=lambda tup: tup[1])[0]
        true_value = round((popular_buy_price + popular_sell_price) / 2)
        
        # 获取当前持仓
        position = state.position.get(symbol, 0)
        to_buy = position_limit - position
        to_sell = position_limit + position
        
        # 更新滑动窗口
        window.append(abs(position) == position_limit)
        
        # 判断是否需要清算
        soft_liquidate = len(window) == 10 and sum(window) >= 5 and window[-1]
        hard_liquidate = len(window) == 10 and all(window)
        
        # 根据持仓调整买卖价格
        max_buy_price = true_value - 1 if position > position_limit * 0.5 else true_value
        min_sell_price = true_value + 1 if position < position_limit * -0.5 else true_value
        
        # 尝试以有利价格成交对方订单
        for price, volume in sell_orders:
            if to_buy > 0 and price <= max_buy_price:
                quantity = min(to_buy, -volume)
                orders.append(Order(symbol, price, quantity))
                to_buy -= quantity
        
        # 如果触发清算条件，增加更激进的订单
        if to_buy > 0 and hard_liquidate:
            quantity = to_buy // 2
            orders.append(Order(symbol, true_value, quantity))
            to_buy -= quantity
        
        if to_buy > 0 and soft_liquidate:
            quantity = to_buy // 2
            orders.append(Order(symbol, true_value - 2, quantity))
            to_buy -= quantity
        
        # 放置剩余的买单
        if to_buy > 0:
            popular_buy_price = max(buy_orders, key=lambda tup: tup[1])[0]
            price = min(max_buy_price, popular_buy_price + 1)
            orders.append(Order(symbol, price, to_buy))
        
        # 卖出逻辑，类似于买入
        for price, volume in buy_orders:
            if to_sell > 0 and price >= min_sell_price:
                quantity = min(to_sell, volume)
                orders.append(Order(symbol, price, -quantity))
                to_sell -= quantity
        
        if to_sell > 0 and hard_liquidate:
            quantity = to_sell // 2
            orders.append(Order(symbol, true_value, -quantity))
            to_sell -= quantity
        
        if to_sell > 0 and soft_liquidate:
            quantity = to_sell // 2
            orders.append(Order(symbol, true_value + 2, -quantity))
            to_sell -= quantity
        
        if to_sell > 0:
            popular_sell_price = min(sell_orders, key=lambda tup: tup[1])[0]
            price = max(min_sell_price, popular_sell_price - 1)
            orders.append(Order(symbol, price, -to_sell))
        
        return orders
