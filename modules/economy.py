"""
AMM 做市商经济系统

参考 AIvilization 的经济系统设计：
- 每个商品有独立的 AMM 流动性池（恒定乘积公式 x * y = k）
- 价格滑点（大单影响市场）
- 宏观价格指数追踪通胀
- 多层商品分类（食物/材料/制品/珍品）
- 事件影响传导

保留 EconomySystem 类名以保持向后兼容，内部使用 AMM 引擎。
"""
from __future__ import annotations
import logging
import math
import random
from .schemas import Economy, AMMPoolState, WorldState

logger = logging.getLogger("chronoverse.economy")


# ── 默认商品配置 ──────────────────────────────────────────

# [v9] 商品中文名称映射
COMMODITY_CN_NAMES = {
    "rice": "大米", "fish": "鲜鱼", "meat": "猪肉", "vegetable": "蔬菜",
    "tea": "茶叶", "wine": "好酒", "wood": "木材", "iron": "铁矿",
    "cloth": "布匹", "silk": "丝绸", "sword": "利剑", "medicine": "丹药",
    "book": "书籍", "artifact": "古董", "spirit_stone": "灵石",
}

DEFAULT_COMMODITIES = {
    # 基础食物
    "rice":       {"base_price": 10,  "init_commodity": 5000, "category": "food", "tier": 1},
    "fish":       {"base_price": 15,  "init_commodity": 3000, "category": "food", "tier": 1},
    "meat":       {"base_price": 25,  "init_commodity": 2000, "category": "food", "tier": 1},
    "vegetable":  {"base_price": 8,   "init_commodity": 6000, "category": "food", "tier": 1},
    "tea":        {"base_price": 20,  "init_commodity": 2500, "category": "food", "tier": 2},
    "wine":       {"base_price": 40,  "init_commodity": 1500, "category": "food", "tier": 2},
    # 原材料
    "wood":       {"base_price": 12,  "init_commodity": 4000, "category": "material", "tier": 1},
    "iron":       {"base_price": 30,  "init_commodity": 2000, "category": "material", "tier": 1},
    "cloth":      {"base_price": 18,  "init_commodity": 3000, "category": "material", "tier": 1},
    "silk":       {"base_price": 50,  "init_commodity": 1000, "category": "material", "tier": 2},
    # 制成品
    "sword":      {"base_price": 80,  "init_commodity": 500,  "category": "product", "tier": 2},
    "medicine":   {"base_price": 60,  "init_commodity": 800,  "category": "product", "tier": 2},
    "book":       {"base_price": 35,  "init_commodity": 1200, "category": "product", "tier": 2},
    # 珍品
    "artifact":   {"base_price": 200, "init_commodity": 200,  "category": "luxury", "tier": 3},
    "spirit_stone": {"base_price": 500, "init_commodity": 100, "category": "luxury", "tier": 3},
}


class AMMPool:
    """单个商品的 AMM 流动性池（恒定乘积做市商）"""

    def __init__(self, commodity_reserve: float, currency_reserve: float,
                 category: str = "misc", tier: int = 1):
        self.commodity_reserve = commodity_reserve
        self.currency_reserve = currency_reserve
        self.constant_product = commodity_reserve * currency_reserve
        self.category = category
        self.tier = tier
        self.total_trades = 0
        self.volume_24h = 0.0
        self._price_history: list[float] = [currency_reserve / commodity_reserve
                                            if commodity_reserve > 0 else 0]

    @classmethod
    def from_state(cls, state: AMMPoolState, category: str = "misc",
                   tier: int = 1) -> AMMPool:
        pool = cls(state.commodity_reserve, state.currency_reserve,
                   category, tier)
        pool.constant_product = state.constant_product
        pool.total_trades = state.total_trades
        pool.volume_24h = state.volume_24h
        return pool

    def to_state(self) -> AMMPoolState:
        return AMMPoolState(
            commodity_reserve=self.commodity_reserve,
            currency_reserve=self.currency_reserve,
            constant_product=self.constant_product,
            total_trades=self.total_trades,
            volume_24h=self.volume_24h,
        )

    @property
    def price(self) -> float:
        """当前即时价格 = 货币储备 / 商品储备"""
        if self.commodity_reserve <= 0:
            return float('inf')
        return self.currency_reserve / self.commodity_reserve

    def get_buy_quote(self, currency_in: float) -> dict:
        """买入报价：投入货币，获得多少商品"""
        if currency_in <= 0:
            return {"amount": 0, "price_per_unit": self.price, "slippage": 0}
        new_currency = self.currency_reserve + currency_in
        new_commodity = self.constant_product / new_currency
        commodity_out = self.commodity_reserve - new_commodity
        if commodity_out <= 0:
            return {"amount": 0, "price_per_unit": self.price, "slippage": 0}
        effective_price = currency_in / commodity_out
        slippage = ((effective_price - self.price) / self.price
                    if self.price > 0 else 0)
        return {"amount": commodity_out, "price_per_unit": effective_price,
                "slippage": slippage}

    def get_sell_quote(self, commodity_in: float) -> dict:
        """卖出报价：投入商品，获得多少货币"""
        if commodity_in <= 0:
            return {"amount": 0, "price_per_unit": self.price, "slippage": 0}
        new_commodity = self.commodity_reserve + commodity_in
        new_currency = self.constant_product / new_commodity
        currency_out = self.currency_reserve - new_currency
        if currency_out <= 0:
            return {"amount": 0, "price_per_unit": self.price, "slippage": 0}
        effective_price = currency_out / commodity_in
        slippage = ((self.price - effective_price) / self.price
                    if self.price > 0 else 0)
        return {"amount": currency_out, "price_per_unit": effective_price,
                "slippage": slippage}

    def execute_buy(self, currency_in: float) -> dict:
        """执行买入"""
        quote = self.get_buy_quote(currency_in)
        commodity_out = quote["amount"]
        if commodity_out <= 0:
            return {"success": False, "error": "流动性不足"}
        self.commodity_reserve -= commodity_out
        self.currency_reserve += currency_in
        self.total_trades += 1
        self.volume_24h += currency_in
        self._record_price()
        return {"success": True, "amount": int(commodity_out),
                "price_per_unit": quote["price_per_unit"],
                "total_cost": currency_in, "slippage": quote["slippage"],
                "new_price": self.price}

    def execute_sell(self, commodity_in: float) -> dict:
        """执行卖出"""
        quote = self.get_sell_quote(commodity_in)
        currency_out = quote["amount"]
        if currency_out <= 0:
            return {"success": False, "error": "流动性不足"}
        self.commodity_reserve += commodity_in
        self.currency_reserve -= currency_out
        self.total_trades += 1
        self.volume_24h += currency_out
        self._record_price()
        return {"success": True, "amount": int(commodity_in),
                "price_per_unit": quote["price_per_unit"],
                "total_cost": currency_out, "slippage": quote["slippage"],
                "new_price": self.price}

    def _record_price(self):
        self._price_history.append(self.price)
        if len(self._price_history) > 1000:
            self._price_history = self._price_history[-500:]


class EconomySystem:
    """AMM 做市商经济系统（保持向后兼容的类名）"""

    def __init__(self):
        self.pools: dict[str, AMMPool] = {}
        self.commodity_config: dict[str, dict] = DEFAULT_COMMODITIES
        self.macro_price_index: float = 1.0
        self.original_base_prices: dict[str, float] = {}
        self.price_history: dict[str, list[float]] = {}  # 兼容旧接口

    def initialize(self, economy: Economy):
        """从 Economy 模型初始化 AMM 池"""
        # 保存原始价格用于通胀计算
        self.original_base_prices = dict(economy.base_prices)

        if economy.amm_pools:
            # 从保存的状态恢复
            for item, pool_state in economy.amm_pools.items():
                cat = self.commodity_config.get(item, {}).get("category", "misc")
                tier = self.commodity_config.get(item, {}).get("tier", 1)
                self.pools[item] = AMMPool.from_state(pool_state, cat, tier)
        else:
            # 创建新池（优先使用 economy.base_prices 中已有的商品）
            if economy.base_prices:
                for item, base_price in economy.base_prices.items():
                    if item in self.commodity_config:
                        cfg = self.commodity_config[item]
                    else:
                        cfg = {"base_price": base_price, "init_commodity": 1000,
                               "category": "misc", "tier": 1}
                    init_commodity = cfg["init_commodity"]
                    init_currency = base_price * init_commodity
                    self.pools[item] = AMMPool(
                        init_commodity, init_currency,
                        cfg.get("category", "misc"), cfg.get("tier", 1))
            else:
                # 使用默认配置
                for item, cfg in self.commodity_config.items():
                    init_commodity = cfg["init_commodity"]
                    init_currency = cfg["base_price"] * init_commodity
                    self.pools[item] = AMMPool(
                        init_commodity, init_currency,
                        cfg.get("category", "misc"), cfg.get("tier", 1))
                # 同步到 economy.base_prices
                for item, pool in self.pools.items():
                    economy.base_prices[item] = pool.price

        self.macro_price_index = economy.macro_price_index or 1.0
        # 初始化 price_history 兼容层
        for item, pool in self.pools.items():
            self.price_history[item] = [pool.price]
        logger.info("AMM经济系统初始化: %d个商品池", len(self.pools))

    def update_prices(self, economy: Economy, world_state: WorldState,
                      event_type: str = "") -> dict:
        """更新价格：应用季节/事件/危机修正 + 随机波动"""
        changes = {}
        for item, pool in self.pools.items():
            old_price = pool.price

            # 季节修正
            seasonal_mod = self._seasonal_modifier(item, world_state.season)
            # 事件修正
            event_mod = self._event_modifier(event_type)
            # 危机修正
            crisis_mod = 1.0 + (world_state.crisis_level * 0.02)
            # 随机波动
            random_mod = random.uniform(0.97, 1.03)

            total_mod = seasonal_mod * event_mod * crisis_mod * random_mod

            if total_mod != 1.0:
                # 通过调整流动性来影响价格
                adjustment = pool.currency_reserve * (total_mod - 1.0) * 0.1
                pool.currency_reserve += adjustment
                pool.constant_product = pool.commodity_reserve * pool.currency_reserve

            new_price = pool.price
            pool._record_price()

            if abs(new_price - old_price) > old_price * 0.02:
                changes[item] = {"old": round(old_price, 1),
                                 "new": round(new_price, 1)}

            # 更新兼容层
            economy.base_prices[item] = new_price
            self.price_history[item] = self.price_history.get(item, [])
            self.price_history[item].append(new_price)
            if len(self.price_history[item]) > 30:
                self.price_history[item] = self.price_history[item][-30:]

        self._sync_to_economy(economy)
        return changes

    def get_price(self, economy: Economy, item: str) -> float:
        pool = self.pools.get(item)
        return pool.price if pool else economy.base_prices.get(item, 0)

    def get_price_trend(self, item: str) -> str:
        pool = self.pools.get(item)
        if not pool:
            history = self.price_history.get(item, [])
            if len(history) < 3:
                return "稳定"
            return "上涨" if history[-1] > history[0] * 1.1 else (
                "下跌" if history[-1] < history[0] * 0.9 else "稳定")
        history = pool._price_history
        if len(history) < 10:
            return "稳定"
        recent_avg = sum(history[-5:]) / 5
        older_avg = sum(history[-10:-5]) / 5 if len(history) >= 10 else recent_avg
        change = (recent_avg - older_avg) / older_avg if older_avg > 0 else 0
        if change > 0.02:
            return "上涨"
        elif change < -0.02:
            return "下跌"
        return "稳定"

    def apply_player_trade(self, economy: Economy, item: str,
                           quantity: int, is_buying: bool) -> dict:
        """执行玩家交易（使用 AMM）"""
        pool = self.pools.get(item)
        if not pool:
            # 回退到简单定价
            price = economy.base_prices.get(item, 10)
            total = price * quantity
            return {"item": item, "quantity": quantity, "unit_price": price,
                    "total_cost": total, "is_buying": is_buying}

        if is_buying:
            # 玩家买入：投入 quantity 个货币单位
            total_currency = quantity * pool.price  # 预估
            result = pool.execute_buy(total_currency)
        else:
            # 玩家卖出：投入 quantity 个商品
            result = pool.execute_sell(float(quantity))

        # 同步到 economy
        economy.base_prices[item] = pool.price
        self._sync_to_economy(economy)

        return {
            "item": item,
            "quantity": result.get("amount", quantity),
            "unit_price": result.get("price_per_unit", pool.price),
            "total_cost": result.get("total_cost", 0),
            "is_buying": is_buying,
            "slippage": result.get("slippage", 0),
            "new_price": pool.price,
            "success": result.get("success", False),
        }

    def execute_trade(self, item: str, amount: float, is_buy: bool) -> dict:
        """通用交易接口"""
        pool = self.pools.get(item)
        if not pool:
            return {"success": False, "error": f"未知商品: {item}"}
        if is_buy:
            return pool.execute_buy(amount)
        return pool.execute_sell(amount)

    def get_market_report(self, economy: Economy = None) -> str:
        """生成市场报告"""
        lines = ["【AMM市场行情报告】\n"]

        categories = {"food": "粮食", "material": "材料",
                      "product": "制品", "luxury": "珍品"}
        for cat, cat_name in categories.items():
            items = [(n, p) for n, p in self.pools.items() if p.category == cat]
            if not items:
                continue
            lines.append(f"—— {cat_name} ——")
            for name, pool in items:
                trend = self.get_price_trend(name)
                icon = {"上涨": "📈", "下跌": "📉", "稳定": "➡️"}.get(trend, "➡️")
                # [v9] 使用中文名称
                cn_name = COMMODITY_CN_NAMES.get(name, name)
                lines.append(
                    f"  {cn_name}: {pool.price:.1f}文 {icon}"
                    f" (储备:{pool.commodity_reserve:.0f} 交易:{pool.total_trades})")

        lines.append(f"\n宏观价格指数: {self.macro_price_index:.3f}")

        # 食品/非食品通胀
        food_items = [n for n, p in self.pools.items() if p.category == "food"]
        non_food = [n for n, p in self.pools.items() if p.category != "food"]
        if food_items:
            fi = self._calc_category_inflation(food_items)
            lines.append(f"食品通胀: {fi:.3f}")
        if non_food:
            nfi = self._calc_category_inflation(non_food)
            lines.append(f"非食品通胀: {nfi:.3f}")

        return "\n".join(lines)

    def apply_event_impact(self, event_type: str, severity: int = 5,
                           economy: Economy = None):
        """根据世界事件调整经济"""
        modifiers = {
            "combat":    {"food": 1.3, "material": 1.1, "product": 1.2, "luxury": 0.9},
            "politics":  {"food": 1.1, "material": 1.0, "product": 1.0, "luxury": 1.2},
            "natural":   {"food": 1.5, "material": 1.3, "product": 1.2, "luxury": 1.0},
            "economic":  {"food": 1.1, "material": 1.2, "product": 1.3, "luxury": 1.4},
            "social":    {"food": 1.0, "material": 1.0, "product": 1.1, "luxury": 1.1},
            "crime":     {"food": 1.1, "material": 1.1, "product": 1.2, "luxury": 1.3},
        }
        mods = modifiers.get(event_type, {})
        severity_factor = 1.0 + (severity - 5) * 0.02

        for name, pool in self.pools.items():
            mod = mods.get(pool.category, 1.0) * severity_factor
            if mod != 1.0:
                adjustment = pool.currency_reserve * (mod - 1.0) * 0.1
                pool.currency_reserve += adjustment
                pool.constant_product = pool.commodity_reserve * pool.currency_reserve

        if economy:
            self._sync_to_economy(economy)

    def get_stylized_facts(self) -> dict:
        """计算市场统计特征（厚尾分布、波动聚集等）"""
        facts = {}
        for name, pool in self.pools.items():
            history = pool._price_history
            if len(history) < 20:
                continue
            returns = []
            for i in range(1, len(history)):
                if history[i-1] > 0 and history[i] > 0:
                    returns.append(math.log(history[i] / history[i-1]))
            if not returns:
                continue
            mean_r = sum(returns) / len(returns)
            var_r = sum((r - mean_r)**2 for r in returns) / len(returns)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.001
            skew = sum(((r - mean_r) / std_r)**3 for r in returns) / len(returns)
            kurt = sum(((r - mean_r) / std_r)**4 for r in returns) / len(returns) - 3
            facts[name] = {
                "mean_return": round(mean_r, 6),
                "std_return": round(std_r, 6),
                "skewness": round(skew, 3),
                "excess_kurtosis": round(kurt, 3),
                "total_trades": pool.total_trades,
                "price_range": round(max(history) - min(history), 2),
            }
        return facts

    # ── 内部方法 ──────────────────────────────────────────

    def _sync_to_economy(self, economy: Economy):
        """将 AMM 状态同步回 Economy 模型"""
        for item, pool in self.pools.items():
            economy.amm_pools[item] = pool.to_state()
            economy.base_prices[item] = pool.price
            economy.supply_demand[item] = pool.commodity_reserve
        economy.macro_price_index = self.macro_price_index
        food_items = [n for n, p in self.pools.items() if p.category == "food"]
        non_food = [n for n, p in self.pools.items() if p.category != "food"]
        economy.food_inflation = self._calc_category_inflation(food_items)
        economy.non_food_inflation = self._calc_category_inflation(non_food)

    def _calc_category_inflation(self, items: list[str]) -> float:
        if not items:
            return 1.0
        ratios = []
        for item in items:
            pool = self.pools.get(item)
            base = self.original_base_prices.get(item,
                   self.commodity_config.get(item, {}).get("base_price", 0))
            if pool and base > 0:
                ratios.append(pool.price / base)
        if not ratios:
            return 1.0
        log_sum = sum(math.log(r) for r in ratios if r > 0)
        return round(math.exp(log_sum / len(ratios)), 3)

    def _seasonal_modifier(self, item: str, season: str) -> float:
        pool = self.pools.get(item)
        cat = pool.category if pool else "misc"
        mods = {
            "春季": {"food": 0.95, "material": 1.0, "product": 1.0, "luxury": 1.0},
            "夏季": {"food": 1.05, "material": 0.95, "product": 1.0, "luxury": 1.0},
            "秋季": {"food": 0.85, "material": 1.0, "product": 1.0, "luxury": 1.05},
            "冬季": {"food": 1.15, "material": 1.1, "product": 1.05, "luxury": 1.0},
        }
        return mods.get(season, {}).get(cat, 1.0)

    def _event_modifier(self, event_type: str) -> float:
        return {"combat": 1.2, "politics": 1.05, "natural": 1.3,
                "economic": 1.15, "social": 1.0, "crime": 1.1}.get(event_type, 1.0)

    def _calculate_inflation(self, economy: Economy) -> float:
        if not economy.base_prices:
            return 1.0
        total = sum(economy.base_prices.values())
        base_total = sum(self.original_base_prices.values()) if self.original_base_prices else total
        return round(total / base_total, 3) if base_total > 0 else 1.0
