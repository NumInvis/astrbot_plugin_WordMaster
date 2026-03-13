"""
Wordle + Handle 合并版游戏插件
移植自 nonebot-plugin-wordle 和 nonebot-plugin-handle
版本: 1.0.0
"""

import asyncio
import random
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain
import astrbot.core.message.components as Comp


class GameType(Enum):
    """游戏类型"""
    WORDLE = "wordle"      # 英文猜单词
    HANDLE = "handle"      # 汉字猜成语


class GameState(Enum):
    """游戏状态"""
    WAITING = "waiting"    # 等待开始
    PLAYING = "playing"    # 游戏中
    FINISHED = "finished"  # 已结束


@dataclass
class GameSession:
    """游戏会话"""
    game_type: GameType
    answer: str
    guesses: List[str] = field(default_factory=list)
    state: GameState = GameState.PLAYING
    max_attempts: int = 6
    strict_mode: bool = False  # 严格模式（仅用于handle）
    
    def is_finished(self) -> bool:
        """检查游戏是否结束"""
        if self.state == GameState.FINISHED:
            return True
        if len(self.guesses) >= self.max_attempts:
            return True
        if self.guesses and self.guesses[-1] == self.answer:
            return True
        return False
    
    def is_won(self) -> bool:
        """检查是否获胜"""
        return self.guesses and self.guesses[-1] == self.answer


@register("astrbot_plugin_wordle_handle", "NumInvis", "Wordle + Handle 合并版游戏插件", "1.0.0")
class WordleHandlePlugin(Star):
    """Wordle + Handle 游戏插件"""
    
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        
        # 游戏会话管理 {session_id: GameSession}
        self.games: Dict[str, GameSession] = {}
        
        # 加载词库
        self.word_lists = self._load_word_lists()
        self.idiom_list = self._load_idiom_list()
        
        logger.info("Wordle + Handle 插件已初始化")
    
    def _load_word_lists(self) -> Dict[str, List[str]]:
        """加载英文单词库"""
        # 默认CET4词库（简化版）
        default_words = [
            "apple", "beach", "chair", "dance", "eagle", "flame", "grape", "house",
            "image", "judge", "knife", "lemon", "music", "night", "ocean", "piano",
            "queen", "radio", "sheep", "table", "uncle", "video", "water", "youth",
            "zebra", "bread", "cloud", "dream", "earth", "fruit", "green", "heart",
            "island", "juice", "light", "money", "north", "orange", "peace", "quiet",
            "river", "smile", "tiger", "unity", "voice", "watch", "young", "alarm",
            "brush", "camel", "dress", "entry", "focus", "glass", "hotel", "input",
            "jelly", "kite", "lunch", "match", "novel", "order", "paint", "quote",
            "rider", "scale", "toast", "urban", "visit", "whale", "yield", "zone"
        ]
        
        return {
            "CET4": default_words,
            "CET6": default_words + ["abroad", "accept", "access", "across", "action"],
            "GRE": default_words + ["abate", "abdicate", "aberration", "abhor"],
            "IELTS": default_words + ["academic", "academy", "accelerate", "accent"],
            "TOEFL": default_words + ["abandon", "ability", "absence", "absolute"],
        }
    
    def _load_idiom_list(self) -> List[str]:
        """加载成语库"""
        # 常用成语库（简化版）
        return [
            "一心一意", "二话不说", "三心二意", "四面八方", "五湖四海",
            "六神无主", "七上八下", "八仙过海", "九牛一毛", "十全十美",
            "画蛇添足", "守株待兔", "亡羊补牢", "掩耳盗铃", "买椟还珠",
            "自相矛盾", "刻舟求剑", "狐假虎威", "井底之蛙", "杯弓蛇影",
            "画龙点睛", "对牛弹琴", "望梅止渴", "卧薪尝胆", "破釜沉舟",
            "草木皆兵", "纸上谈兵", "指鹿为马", "四面楚歌", "背水一战",
            "一鸣惊人", "一鼓作气", "半途而废", "锲而不舍", "水滴石穿",
            "胸有成竹", "熟能生巧", "举一反三", "触类旁通", "融会贯通",
            "学以致用", "温故知新", "不耻下问", "孜孜不倦", "精益求精",
            "实事求是", "脚踏实地", "持之以恒", "坚持不懈", "全力以赴"
        ]
    
    def _get_session_id(self, event: AstrMessageEvent) -> str:
        """获取会话ID"""
        return event.unified_msg_origin
    
    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查是否是管理员"""
        # 获取配置中的管理员列表
        config = self.context.get_config()
        admin_users = config.get("admin_users", []) if config else []
        
        # 获取用户ID
        user_id = str(event.get_sender_id())
        
        # 检查是否在管理员列表中
        return user_id in admin_users
    
    # ========== Wordle 游戏逻辑 ==========
    
    def _check_wordle_guess(self, guess: str, answer: str) -> List[Tuple[str, str]]:
        """
        检查 Wordle 猜测结果
        返回: [(字符, 状态), ...]
        状态: G=绿色(正确位置), Y=黄色(存在但位置错), X=灰色(不存在)
        """
        result = []
        answer_chars = list(answer)
        guess_chars = list(guess)
        
        # 第一遍：标记绿色（正确位置）
        for i, (g, a) in enumerate(zip(guess_chars, answer_chars)):
            if g == a:
                result.append((g, "G"))
                answer_chars[i] = None  # 标记为已使用
                guess_chars[i] = None
            else:
                result.append((g, "X"))  # 先标记为灰色
        
        # 第二遍：标记黄色（存在但位置错）
        for i, g in enumerate(guess_chars):
            if g is None:
                continue  # 跳过已标记为绿色的
            if g in answer_chars:
                result[i] = (g, "Y")
                answer_chars[answer_chars.index(g)] = None  # 标记为已使用
        
        return result
    
    def _format_wordle_result(self, results: List[List[Tuple[str, str]]]) -> str:
        """格式化 Wordle 结果为可视化字符串"""
        lines = []
        for result in results:
            line = ""
            for char, status in result:
                if status == "G":
                    line += f"🟩{char.upper()}"  # 绿色
                elif status == "Y":
                    line += f"🟨{char.upper()}"  # 黄色
                else:
                    line += f"⬜{char.upper()}"  # 灰色
            lines.append(line)
        return "\n".join(lines)
    
    # ========== Handle 游戏逻辑 ==========
    
    def _get_pinyin(self, char: str) -> str:
        """获取汉字的拼音（简化版）"""
        # 常用汉字拼音映射（简化版）
        pinyin_map = {
            "一": "yi1", "二": "er4", "三": "san1", "四": "si4", "五": "wu3",
            "六": "liu4", "七": "qi1", "八": "ba1", "九": "jiu3", "十": "shi2",
            "心": "xin1", "意": "yi4", "画": "hua4", "蛇": "she2", "添": "tian1",
            "足": "zu2", "守": "shou3", "株": "zhu1", "待": "dai4", "兔": "tu4",
            "亡": "wang2", "羊": "yang2", "补": "bu3", "牢": "lao2", "掩": "yan3",
            "耳": "er3", "盗": "dao4", "铃": "ling2", "买": "mai3", "椟": "du2",
            "还": "huan2", "珠": "zhu1", "自": "zi4", "相": "xiang1", "矛": "mao2",
            "盾": "dun4", "刻": "ke4", "舟": "zhou1", "求": "qiu2", "剑": "jian4",
            "狐": "hu2", "假": "jia3", "虎": "hu3", "威": "wei1", "井": "jing3",
            "底": "di3", "之": "zhi1", "蛙": "wa1", "杯": "bei1", "弓": "gong1",
            "影": "ying3", "龙": "long2", "点": "dian3", "睛": "jing1", "对": "dui4",
            "牛": "niu2", "弹": "tan2", "琴": "qin2", "望": "wang4", "梅": "mei2",
            "止": "zhi3", "渴": "ke3", "卧": "wo4", "薪": "xin1", "尝": "chang2",
            "胆": "dan3", "破": "po4", "釜": "fu3", "沉": "chen2", "舟": "zhou1",
            "草": "cao3", "木": "mu4", "皆": "jie1", "兵": "bing1", "纸": "zhi3",
            "上": "shang4", "谈": "tan2", "兵": "bing1", "指": "zhi3", "鹿": "lu4",
            "为": "wei2", "马": "ma3", "四": "si4", "面": "mian4", "楚": "chu3",
            "歌": "ge1", "背": "bei4", "水": "shui3", "一": "yi1", "战": "zhan4",
            "鸣": "ming2", "惊": "jing1", "人": "ren2", "鼓": "gu3", "作": "zuo4",
            "气": "qi4", "半": "ban4", "途": "tu2", "而": "er2", "废": "fei4",
            "锲": "qie4", "而": "er2", "不": "bu4", "舍": "she3", "水": "shui3",
            "滴": "di1", "石": "shi2", "穿": "chuan1", "胸": "xiong1", "有": "you3",
            "成": "cheng2", "竹": "zhu2", "熟": "shu2", "能": "neng2", "生": "sheng1",
            "巧": "qiao3", "举": "ju3", "一": "yi1", "反": "fan3", "三": "san1",
            "触": "chu4", "类": "lei4", "旁": "pang2", "通": "tong1", "融": "rong2",
            "会": "hui4", "贯": "guan4", "通": "tong1", "学": "xue2", "以": "yi3",
            "致": "zhi4", "用": "yong4", "温": "wen1", "故": "gu4", "知": "zhi1",
            "新": "xin1", "不": "bu4", "耻": "chi3", "下": "xia4", "问": "wen4",
            "孜": "zi1", "孜": "zi1", "不": "bu4", "倦": "juan4", "精": "jing1",
            "益": "yi4", "求": "qiu2", "精": "jing1", "实": "shi2", "事": "shi4",
            "求": "qiu2", "是": "shi4", "脚": "jiao3", "踏": "ta4", "实": "shi2",
            "地": "di4", "持": "chi2", "之": "zhi1", "以": "yi3", "恒": "heng2",
            "坚": "jian1", "持": "chi2", "不": "bu4", "懈": "xie4", "全": "quan2",
            "力": "li4", "以": "yi3", "赴": "fu4", "说": "shuo1", "话": "hua4",
            "方": "fang1", "便": "bian4", "不": "bu4", "方": "fang1", "便": "bian4",
        }
        return pinyin_map.get(char, char)
    
    def _parse_pinyin(self, pinyin: str) -> Tuple[str, str, str]:
        """解析拼音为声母、韵母、声调"""
        # 简化处理：假设拼音格式为 "pin1" 或 "yin"
        if not pinyin:
            return ("", "", "")
        
        # 提取声调（最后一个字符如果是数字）
        tone = ""
        if pinyin[-1].isdigit():
            tone = pinyin[-1]
            pinyin = pinyin[:-1]
        
        # 简化为声母和韵母（这里简化处理）
        # 实际应该根据拼音规则分割
        if len(pinyin) >= 2:
            return (pinyin[0], pinyin[1:], tone)
        else:
            return (pinyin, "", tone)
    
    def _check_handle_guess(self, guess: str, answer: str) -> List[List[Tuple[str, str]]]:
        """
        检查 Handle 猜测结果
        返回: [[(汉字, 状态), (声母, 状态), (韵母, 状态), (声调, 状态)], ...]
        状态: G=绿色, Y=黄色, X=灰色
        """
        result = []
        answer_chars = list(answer)
        guess_chars = list(guess)
        
        # 检查每个字
        for i, (g_char, a_char) in enumerate(zip(guess_chars, answer_chars)):
            char_result = []
            
            # 获取拼音
            g_pinyin = self._get_pinyin(g_char)
            a_pinyin = self._get_pinyin(a_char)
            
            g_sheng, g_yun, g_tone = self._parse_pinyin(g_pinyin)
            a_sheng, a_yun, a_tone = self._parse_pinyin(a_pinyin)
            
            # 检查汉字
            if g_char == a_char:
                char_result.append((g_char, "G"))
            elif g_char in answer_chars:
                char_result.append((g_char, "Y"))
            else:
                char_result.append((g_char, "X"))
            
            # 检查声母
            if g_sheng == a_sheng:
                char_result.append((g_sheng, "G"))
            elif g_sheng and any(self._parse_pinyin(self._get_pinyin(c))[0] == g_sheng for c in answer_chars):
                char_result.append((g_sheng, "Y"))
            else:
                char_result.append((g_sheng if g_sheng else "-", "X"))
            
            # 检查韵母
            if g_yun == a_yun:
                char_result.append((g_yun, "G"))
            elif g_yun and any(self._parse_pinyin(self._get_pinyin(c))[1] == g_yun for c in answer_chars):
                char_result.append((g_yun, "Y"))
            else:
                char_result.append((g_yun if g_yun else "-", "X"))
            
            # 检查声调
            if g_tone == a_tone:
                char_result.append((g_tone if g_tone else "-", "G"))
            elif g_tone and any(self._parse_pinyin(self._get_pinyin(c))[2] == g_tone for c in answer_chars):
                char_result.append((g_tone, "Y"))
            else:
                char_result.append((g_tone if g_tone else "-", "X"))
            
            result.append(char_result)
        
        return result
    
    def _format_handle_result(self, results: List[List[List[Tuple[str, str]]]]) -> str:
        """格式化 Handle 结果为可视化字符串"""
        lines = []
        for result in results:
            line = ""
            for item in result:
                char, status = item
                if status == "G":
                    line += f"🟩{char}"
                elif status == "Y":
                    line += f"🟨{char}"
                else:
                    line += f"⬜{char}"
            lines.append(line)
        return "\n".join(lines)
    
    # ========== 命令处理 ==========
    
    @filter.command("wordle")
    async def cmd_wordle(self, event: AstrMessageEvent, dictionary: str = "CET4", length: int = 5):
        """开始 Wordle 猜单词游戏"""
        session_id = self._get_session_id(event)
        
        # 检查是否已有游戏在进行
        if session_id in self.games and self.games[session_id].state == GameState.PLAYING:
            yield event.plain_result("❌ 当前已有游戏在进行中，请先结束当前游戏")
            return
        
        # 验证词典
        dictionary = dictionary.upper()
        if dictionary not in self.word_lists:
            available = ", ".join(self.word_lists.keys())
            yield event.plain_result(f"❌ 不支持的词典: {dictionary}\n可用词典: {available}")
            return
        
        # 选择单词
        word_list = self.word_lists[dictionary]
        valid_words = [w for w in word_list if len(w) == length]
        
        if not valid_words:
            yield event.plain_result(f"❌ 词典 {dictionary} 中没有长度为 {length} 的单词")
            return
        
        answer = random.choice(valid_words)
        
        # 创建游戏会话
        game = GameSession(
            game_type=GameType.WORDLE,
            answer=answer,
            max_attempts=6
        )
        self.games[session_id] = game
        
        msg = f"🎮 Wordle 游戏开始！\n"
        msg += f"📚 词典: {dictionary}\n"
        msg += f"🔤 单词长度: {length}\n"
        msg += f"🎯 你有 {game.max_attempts} 次机会猜出单词\n\n"
        msg += "💡 提示:\n"
        msg += "🟩 绿色 = 字母正确且位置正确\n"
        msg += "🟨 黄色 = 字母存在但位置错误\n"
        msg += "⬜ 灰色 = 字母不存在\n\n"
        msg += "发送你的猜测（如: apple）或发送 \"结束\" 结束游戏"
        
        yield event.plain_result(msg)
    
    @filter.command("handle")
    async def cmd_handle(self, event: AstrMessageEvent, strict: bool = False):
        """开始 Handle 猜成语游戏"""
        session_id = self._get_session_id(event)
        
        # 检查是否已有游戏在进行
        if session_id in self.games and self.games[session_id].state == GameState.PLAYING:
            yield event.plain_result("❌ 当前已有游戏在进行中，请先结束当前游戏")
            return
        
        # 选择成语
        answer = random.choice(self.idiom_list)
        
        # 创建游戏会话
        game = GameSession(
            game_type=GameType.HANDLE,
            answer=answer,
            max_attempts=10,
            strict_mode=strict
        )
        self.games[session_id] = game
        
        msg = f"🎮 Handle 猜成语游戏开始！\n"
        msg += f"🎯 你有 {game.max_attempts} 次机会猜出四字成语\n\n"
        msg += "💡 提示:\n"
        msg += "🟩 绿色 = 正确\n"
        msg += "🟨 黄色 = 存在但位置错误\n"
        msg += "⬜ 灰色 = 不存在\n"
        msg += "每个格子包含: 汉字 + 声母 + 韵母 + 声调\n\n"
        
        if strict:
            msg += "🔒 严格模式已开启，猜测必须是有效成语\n"
        
        msg += "发送你的猜测（如: 一心一意）或发送 \"结束\" 结束游戏"
        
        yield event.plain_result(msg)
    
    @filter.event_message()
    async def on_message(self, event: AstrMessageEvent):
        """处理游戏消息"""
        session_id = self._get_session_id(event)
        
        # 检查是否有进行中的游戏
        if session_id not in self.games:
            return
        
        game = self.games[session_id]
        if game.state != GameState.PLAYING:
            return
        
        # 获取消息文本
        text = event.message_str.strip()
        
        # 处理结束命令
        if text in ["结束", "结束游戏", "退出", "quit", "exit"]:
            game.state = GameState.FINISHED
            yield event.plain_result(f"🛑 游戏已结束\n💡 正确答案是: {game.answer}")
            return
        
        # 处理提示命令
        if text in ["提示", "hint", "help"]:
            if game.game_type == GameType.WORDLE:
                yield event.plain_result(f"💡 提示: 单词长度为 {len(game.answer)}")
            else:
                yield event.plain_result(f"💡 提示: 这是一个四字成语")
            return
        
        # 处理猜测
        if game.game_type == GameType.WORDLE:
            await self._handle_wordle_guess(event, game, text)
        else:
            await self._handle_handle_guess(event, game, text)
    
    async def _handle_wordle_guess(self, event: AstrMessageEvent, game: GameSession, guess: str):
        """处理 Wordle 猜测"""
        # 验证输入
        guess = guess.lower().strip()
        
        if len(guess) != len(game.answer):
            yield event.plain_result(f"❌ 请输入 {len(game.answer)} 个字母的单词")
            return
        
        if not guess.isalpha():
            yield event.plain_result("❌ 只能输入英文字母")
            return
        
        # 记录猜测
        game.guesses.append(guess)
        
        # 检查结果
        results = []
        for g in game.guesses:
            result = self._check_wordle_guess(g, game.answer)
            results.append(result)
        
        # 格式化输出
        result_text = self._format_wordle_result(results)
        
        # 检查游戏状态
        if guess == game.answer:
            game.state = GameState.FINISHED
            msg = f"{result_text}\n\n🎉 恭喜你猜对了！\n"
            msg += f"🏆 用了 {len(game.guesses)}/{game.max_attempts} 次机会"
        elif len(game.guesses) >= game.max_attempts:
            game.state = GameState.FINISHED
            msg = f"{result_text}\n\n😢 游戏结束，次数用尽\n"
            msg += f"💡 正确答案是: {game.answer.upper()}"
        else:
            remaining = game.max_attempts - len(game.guesses)
            msg = f"{result_text}\n\n📝 第 {len(game.guesses)}/{game.max_attempts} 次猜测\n"
            msg += f"💭 还剩 {remaining} 次机会"
        
        yield event.plain_result(msg)
    
    async def _handle_handle_guess(self, event: AstrMessageEvent, game: GameSession, guess: str):
        """处理 Handle 猜测"""
        # 验证输入
        guess = guess.strip()
        
        if len(guess) != 4:
            yield event.plain_result("❌ 请输入四个汉字")
            return
        
        # 严格模式检查（简化版，实际应该检查是否为有效成语）
        if game.strict_mode and guess not in self.idiom_list:
            yield event.plain_result("❌ 严格模式下，猜测必须是有效的四字成语")
            return
        
        # 记录猜测
        game.guesses.append(guess)
        
        # 检查结果
        results = []
        for g in game.guesses:
            result = self._check_handle_guess(g, game.answer)
            results.append(result)
        
        # 格式化输出
        result_text = self._format_handle_result(results)
        
        # 检查游戏状态
        if guess == game.answer:
            game.state = GameState.FINISHED
            msg = f"{result_text}\n\n🎉 恭喜你猜对了！\n"
            msg += f"🏆 用了 {len(game.guesses)}/{game.max_attempts} 次机会"
        elif len(game.guesses) >= game.max_attempts:
            game.state = GameState.FINISHED
            msg = f"{result_text}\n\n😢 游戏结束，次数用尽\n"
            msg += f"💡 正确答案是: {game.answer}"
        else:
            remaining = game.max_attempts - len(game.guesses)
            msg = f"{result_text}\n\n📝 第 {len(game.guesses)}/{game.max_attempts} 次猜测\n"
            msg += f"💭 还剩 {remaining} 次机会"
        
        yield event.plain_result(msg)
    
    @filter.command("结束游戏")
    async def cmd_end_game(self, event: AstrMessageEvent):
        """结束当前游戏"""
        session_id = self._get_session_id(event)
        
        if session_id not in self.games or self.games[session_id].state != GameState.PLAYING:
            yield event.plain_result("❌ 当前没有进行中的游戏")
            return
        
        game = self.games[session_id]
        game.state = GameState.FINISHED
        
        yield event.plain_result(f"🛑 游戏已结束\n💡 正确答案是: {game.answer}")
    
    @filter.command("wordle帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        msg = """🎮 Wordle + Handle 游戏帮助

📋 游戏命令:
/wordle [词典] [长度] - 开始英文猜单词游戏
  示例: /wordle CET4 5
  可用词典: CET4, CET6, GRE, IELTS, TOEFL

/handle [--strict] - 开始汉字猜成语游戏
  --strict: 开启严格模式（猜测必须是成语）

/结束游戏 - 结束当前游戏

🎮 游戏规则:

【Wordle - 英文猜单词】
- 猜一个指定长度的英文单词
- 🟩 绿色 = 字母正确且位置正确
- 🟨 黄色 = 字母存在但位置错误
- ⬜ 灰色 = 字母不存在
- 共6次机会

【Handle - 汉字猜成语】
- 猜一个四字成语
- 🟩 绿色 = 正确
- 🟨 黄色 = 存在但位置错误
- ⬜ 灰色 = 不存在
- 每个格子显示: 汉字 + 声母 + 韵母 + 声调
- 共10次机会

💡 游戏中可用指令:
- "结束" / "退出" - 结束游戏
- "提示" - 获取提示"""
        
        yield event.plain_result(msg)
