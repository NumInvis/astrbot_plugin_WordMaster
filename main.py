"""
WordMaster - 词汇大师游戏插件
移植自 nonebot-plugin-wordle 和 nonebot-plugin-handle
版本: 2.0.0
功能: Wordle英文猜单词 + Handle汉字猜成语 + 排行榜 + 多人对战 + 限时模式
"""

import asyncio
import random
import time
import json
import os
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain
import astrbot.core.message.components as Comp


class GameType(Enum):
    """游戏类型"""
    WORDLE = "wordle"      # 英文猜单词
    HANDLE = "handle"      # 汉字猜成语


class GameMode(Enum):
    """游戏模式"""
    SINGLE = "single"      # 单人模式
    MULTI = "multi"        # 多人对战模式
    TIMED = "timed"        # 限时模式


class GameState(Enum):
    """游戏状态"""
    WAITING = "waiting"    # 等待开始
    PLAYING = "playing"    # 游戏中
    FINISHED = "finished"  # 已结束


@dataclass
class PlayerStats:
    """玩家统计数据"""
    user_id: str
    nickname: str = ""
    total_games: int = 0          # 总游戏数
    wins: int = 0                 # 胜场
    losses: int = 0               # 败场
    best_attempts: int = 999      # 最佳猜测次数（最少）
    total_attempts: int = 0       # 总猜测次数
    win_streak: int = 0           # 当前连胜
    max_win_streak: int = 0       # 最大连胜
    avg_time: float = 0.0         # 平均用时（秒）
    total_time: float = 0.0       # 总用时
    
    def update_win(self, attempts: int, game_time: float):
        """更新胜利统计"""
        self.total_games += 1
        self.wins += 1
        self.win_streak += 1
        self.max_win_streak = max(self.max_win_streak, self.win_streak)
        self.best_attempts = min(self.best_attempts, attempts)
        self.total_attempts += attempts
        self.total_time += game_time
        self.avg_time = self.total_time / self.wins if self.wins > 0 else 0
    
    def update_loss(self, attempts: int, game_time: float):
        """更新失败统计"""
        self.total_games += 1
        self.losses += 1
        self.win_streak = 0
        self.total_attempts += attempts
        self.total_time += game_time


@dataclass
class GameSession:
    """游戏会话"""
    game_type: GameType
    game_mode: GameMode
    answer: str
    host_id: str                    # 房主ID
    players: List[str] = field(default_factory=list)  # 参与玩家列表
    guesses: List[Tuple[str, str]] = field(default_factory=list)  # (玩家ID, 猜测)
    state: GameState = GameState.PLAYING
    max_attempts: int = 6
    strict_mode: bool = False
    time_limit: int = 0             # 限时（秒），0表示不限时
    start_time: float = field(default_factory=time.time)
    used_letters: Set[str] = field(default_factory=set)  # 已使用的字母
    eliminated_letters: Set[str] = field(default_factory=set)  # 已排除的字母
    correct_letters: Dict[int, str] = field(default_factory=dict)  # 位置正确的字母
    winner: Optional[str] = None    # 获胜者
    
    def is_finished(self) -> bool:
        """检查游戏是否结束"""
        if self.state == GameState.FINISHED:
            return True
        if self.game_mode == GameMode.MULTI and self.winner:
            return True
        if len(self.guesses) >= self.max_attempts:
            return True
        if self.guesses and self.guesses[-1][1] == self.answer:
            return True
        if self.time_limit > 0 and time.time() - self.start_time > self.time_limit:
            return True
        return False
    
    def get_remaining_time(self) -> int:
        """获取剩余时间"""
        if self.time_limit <= 0:
            return -1
        remaining = self.time_limit - (time.time() - self.start_time)
        return max(0, int(remaining))


@register("astrbot_plugin_WordMaster", "NumInvis", "WordMaster - 猜词小游戏集合", "1.0.0")
class WordMasterPlugin(Star):
    """WordMaster 词汇大师游戏插件"""
    
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        
        # 游戏会话管理 {session_id: GameSession}
        self.games: Dict[str, GameSession] = {}
        
        # 玩家统计 {user_id: PlayerStats}
        self.player_stats: Dict[str, PlayerStats] = {}
        
        # 加载数据
        self.word_lists = self._load_word_lists()
        self.idiom_list = self._load_idiom_list()
        self.idiom_meanings = self._load_idiom_meanings()
        self._load_stats()
        
        logger.info("WordMaster 词汇大师插件已初始化 v2.0.0")
    
    def _load_word_lists(self) -> Dict[str, List[str]]:
        """加载英文单词库"""
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
    
    def _load_idiom_meanings(self) -> Dict[str, str]:
        """加载成语释义"""
        return {
            "一心一意": "心思、意念专一",
            "二话不说": "不说别的话，立即行动",
            "三心二意": "又想这样又想那样，犹豫不定",
            "四面八方": "各个方面或各个地方",
            "五湖四海": "指全国各地，有时也指世界各地",
            "六神无主": "形容心慌意乱，拿不定主意",
            "七上八下": "形容心里慌乱不安",
            "八仙过海": "比喻各自拿出本领或办法，互相竞赛",
            "九牛一毛": "比喻极大数量中极微小的数量",
            "十全十美": "十分完美，毫无欠缺",
            "画蛇添足": "比喻做了多余的事，非但无益，反而不合适",
            "守株待兔": "比喻死守狭隘经验，不知变通",
            "亡羊补牢": "比喻出了问题以后想办法补救",
            "掩耳盗铃": "比喻自己欺骗自己",
            "买椟还珠": "比喻没有眼力，取舍不当",
            "自相矛盾": "比喻自己说话做事前后抵触",
            "刻舟求剑": "比喻拘泥不知变通",
            "狐假虎威": "比喻依仗别人的势力欺压人",
            "井底之蛙": "比喻见识狭窄的人",
            "杯弓蛇影": "比喻因疑神疑鬼而引起恐惧",
            "画龙点睛": "比喻作文或说话时在关键地方加上精辟的语句",
            "对牛弹琴": "比喻对不懂道理的人讲道理",
            "望梅止渴": "比喻愿望无法实现，用空想安慰自己",
            "卧薪尝胆": "形容人刻苦自励，发奋图强",
            "破釜沉舟": "比喻下决心不顾一切地干到底",
            "草木皆兵": "形容人在惊慌时疑神疑鬼",
            "纸上谈兵": "比喻空谈理论，不能解决实际问题",
            "指鹿为马": "比喻故意颠倒黑白，混淆是非",
            "四面楚歌": "比喻陷入四面受敌的困境",
            "背水一战": "比喻与敌人决一死战",
            "一鸣惊人": "比喻平时没有突出的表现，突然做出惊人的成绩",
            "一鼓作气": "比喻趁劲头大的时候鼓起干劲",
            "半途而废": "比喻做事不能坚持到底",
            "锲而不舍": "比喻有恒心，有毅力",
            "水滴石穿": "比喻只要有恒心，不断努力，事情就一定能成功",
            "胸有成竹": "比喻做事之前已经有通盘的考虑",
            "熟能生巧": "比喻熟练了就能产生巧办法",
            "举一反三": "比喻从一件事情类推而知道其他许多事情",
            "触类旁通": "比喻掌握了某一事物的知识或规律，进而推知同类事物的知识或规律",
            "融会贯通": "把各方面的知识和道理融化汇合，得到全面透彻的理解",
            "学以致用": "为了实际应用而学习",
            "温故知新": "温习旧的知识，得到新的理解和体会",
            "不耻下问": "乐于向学问或地位比自己低的人学习，而不觉得不好意思",
            "孜孜不倦": "指工作或学习勤奋不知疲倦",
            "精益求精": "好了还求更好",
            "实事求是": "指从实际对象出发，探求事物的内部联系及其发展的规律性",
            "脚踏实地": "比喻做事踏实，认真",
            "持之以恒": "长久坚持下去",
            "坚持不懈": "坚持到底，一点不松懈",
            "全力以赴": "把全部力量都投入进去"
        }
    
    def _get_session_id(self, event: AstrMessageEvent) -> str:
        """获取会话ID"""
        return event.unified_msg_origin
    
    def _get_user_id(self, event: AstrMessageEvent) -> str:
        """获取用户ID"""
        return str(event.get_sender_id())
    
    def _get_nickname(self, event: AstrMessageEvent) -> str:
        """获取用户昵称"""
        sender_name = event.get_sender_name()
        return sender_name if sender_name else f"玩家{event.get_sender_id()}"
    
    def _get_or_create_stats(self, user_id: str, nickname: str = "") -> PlayerStats:
        """获取或创建玩家统计"""
        if user_id not in self.player_stats:
            self.player_stats[user_id] = PlayerStats(user_id=user_id, nickname=nickname)
        return self.player_stats[user_id]
    
    def _save_stats(self):
        """保存玩家统计到文件"""
        try:
            stats_file = os.path.join(os.path.dirname(__file__), "wordmaster_stats.json")
            data = {uid: asdict(stats) for uid, stats in self.player_stats.items()}
            with open(stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存统计失败: {e}")
    
    def _load_stats(self):
        """从文件加载玩家统计"""
        try:
            stats_file = os.path.join(os.path.dirname(__file__), "wordmaster_stats.json")
            if os.path.exists(stats_file):
                with open(stats_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for uid, stats_data in data.items():
                        self.player_stats[uid] = PlayerStats(**stats_data)
                logger.info(f"已加载 {len(self.player_stats)} 位玩家统计")
        except Exception as e:
            logger.error(f"加载统计失败: {e}")
    
    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查是否是管理员"""
        config = self.context.get_config()
        admin_users = config.get("admin_users", []) if config else []
        user_id = self._get_user_id(event)
        return user_id in admin_users
    
    # ========== Wordle 游戏逻辑 ==========
    
    def _check_wordle_guess(self, guess: str, answer: str) -> List[Tuple[str, str]]:
        """检查 Wordle 猜测结果"""
        result = []
        answer_chars = list(answer)
        guess_chars = list(guess)
        
        for i, (g, a) in enumerate(zip(guess_chars, answer_chars)):
            if g == a:
                result.append((g, "G"))
                answer_chars[i] = None
                guess_chars[i] = None
            else:
                result.append((g, "X"))
        
        for i, g in enumerate(guess_chars):
            if g is None:
                continue
            if g in answer_chars:
                result[i] = (g, "Y")
                answer_chars[answer_chars.index(g)] = None
        
        return result
    
    def _format_wordle_result(self, results: List[List[Tuple[str, str]]]) -> str:
        """格式化 Wordle 结果"""
        lines = []
        for result in results:
            line = ""
            for char, status in result:
                if status == "G":
                    line += f"🟩{char.upper()}"
                elif status == "Y":
                    line += f"🟨{char.upper()}"
                else:
                    line += f"⬜{char.upper()}"
            lines.append(line)
        return "\n".join(lines)
    
    def _update_hint_system(self, game: GameSession, guess: str, result: List[Tuple[str, str]]):
        """更新提示系统"""
        for i, (char, status) in enumerate(result):
            self.game.used_letters.add(char.lower())
            if status == "G":
                self.game.correct_letters[i] = char
            elif status == "X":
                self.game.eliminated_letters.add(char.lower())
    
    # ========== Handle 游戏逻辑 ==========
    
    def _get_pinyin(self, char: str) -> str:
        """获取汉字拼音（简化版）"""
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
            "胆": "dan3", "破": "po4", "釜": "fu3", "沉": "chen2", "草": "cao3",
            "木": "mu4", "皆": "jie1", "兵": "bing1", "纸": "zhi3", "上": "shang4",
            "谈": "tan2", "指": "zhi3", "鹿": "lu4", "为": "wei2", "马": "ma3",
            "面": "mian4", "楚": "chu3", "歌": "ge1", "背": "bei4", "水": "shui3",
            "战": "zhan4", "鸣": "ming2", "惊": "jing1", "人": "ren2", "鼓": "gu3",
            "作": "zuo4", "气": "qi4", "半": "ban4", "途": "tu2", "而": "er2",
            "废": "fei4", "锲": "qie4", "不": "bu4", "舍": "she3", "滴": "di1",
            "石": "shi2", "穿": "chuan1", "胸": "xiong1", "有": "you3", "成": "cheng2",
            "竹": "zhu2", "熟": "shu2", "能": "neng2", "生": "sheng1", "巧": "qiao3",
            "举": "ju3", "反": "fan3", "触": "chu4", "类": "lei4", "旁": "pang2",
            "通": "tong1", "融": "rong2", "会": "hui4", "贯": "guan4", "学": "xue2",
            "以": "yi3", "致": "zhi4", "用": "yong4", "温": "wen1", "故": "gu4",
            "知": "zhi1", "新": "xin1", "耻": "chi3", "下": "xia4", "问": "wen4",
            "孜": "zi1", "倦": "juan4", "精": "jing1", "益": "yi4", "求": "qiu2",
            "实": "shi2", "事": "shi4", "是": "shi4", "脚": "jiao3", "踏": "ta4",
            "实": "shi2", "地": "di4", "持": "chi2", "恒": "heng2", "坚": "jian1",
            "持": "chi2", "懈": "xie4", "全": "quan2", "力": "li4", "赴": "fu4",
            "说": "shuo1", "话": "hua4", "方": "fang1", "便": "bian4",
        }
        return pinyin_map.get(char, char)
    
    def _parse_pinyin(self, pinyin: str) -> Tuple[str, str, str]:
        """解析拼音"""
        if not pinyin:
            return ("", "", "")
        tone = ""
        if pinyin[-1].isdigit():
            tone = pinyin[-1]
            pinyin = pinyin[:-1]
        if len(pinyin) >= 2:
            return (pinyin[0], pinyin[1:], tone)
        else:
            return (pinyin, "", tone)
    
    def _check_handle_guess(self, guess: str, answer: str) -> List[List[Tuple[str, str]]]:
        """检查 Handle 猜测结果"""
        result = []
        answer_chars = list(answer)
        guess_chars = list(guess)
        
        for i, (g_char, a_char) in enumerate(zip(guess_chars, answer_chars)):
            char_result = []
            g_pinyin = self._get_pinyin(g_char)
            a_pinyin = self._get_pinyin(a_char)
            g_sheng, g_yun, g_tone = self._parse_pinyin(g_pinyin)
            a_sheng, a_yun, a_tone = self._parse_pinyin(a_pinyin)
            
            if g_char == a_char:
                char_result.append((g_char, "G"))
            elif g_char in answer_chars:
                char_result.append((g_char, "Y"))
            else:
                char_result.append((g_char, "X"))
            
            if g_sheng == a_sheng:
                char_result.append((g_sheng, "G"))
            elif g_sheng and any(self._parse_pinyin(self._get_pinyin(c))[0] == g_sheng for c in answer_chars):
                char_result.append((g_sheng, "Y"))
            else:
                char_result.append((g_sheng if g_sheng else "-", "X"))
            
            if g_yun == a_yun:
                char_result.append((g_yun, "G"))
            elif g_yun and any(self._parse_pinyin(self._get_pinyin(c))[1] == g_yun for c in answer_chars):
                char_result.append((g_yun, "Y"))
            else:
                char_result.append((g_yun if g_yun else "-", "X"))
            
            if g_tone == a_tone:
                char_result.append((g_tone if g_tone else "-", "G"))
            elif g_tone and any(self._parse_pinyin(self._get_pinyin(c))[2] == g_tone for c in answer_chars):
                char_result.append((g_tone, "Y"))
            else:
                char_result.append((g_tone if g_tone else "-", "X"))
            
            result.append(char_result)
        
        return result
    
    def _format_handle_result(self, results: List[List[List[Tuple[str, str]]]]) -> str:
        """格式化 Handle 结果"""
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
    async def cmd_wordle(self, event: AstrMessageEvent, dictionary: str = "CET4", length: int = 5, timed: bool = False):
        """开始 Wordle 游戏"""
        session_id = self._get_session_id(event)
        user_id = self._get_user_id(event)
        nickname = self._get_nickname(event)
        
        if session_id in self.games and self.games[session_id].state == GameState.PLAYING:
            yield event.plain_result("❌ 当前已有游戏在进行中，请先结束当前游戏")
            return
        
        dictionary = dictionary.upper()
        if dictionary not in self.word_lists:
            available = ", ".join(self.word_lists.keys())
            yield event.plain_result(f"❌ 不支持的词典: {dictionary}\n可用词典: {available}")
            return
        
        word_list = self.word_lists[dictionary]
        valid_words = [w for w in word_list if len(w) == length]
        
        if not valid_words:
            yield event.plain_result(f"❌ 词典 {dictionary} 中没有长度为 {length} 的单词")
            return
        
        answer = random.choice(valid_words)
        
        game_mode = GameMode.TIMED if timed else GameMode.SINGLE
        time_limit = 300 if timed else 0  # 限时模式5分钟
        
        game = GameSession(
            game_type=GameType.WORDLE,
            game_mode=game_mode,
            answer=answer,
            host_id=user_id,
            players=[user_id],
            max_attempts=6,
            time_limit=time_limit
        )
        self.games[session_id] = game
        
        mode_text = "⏱️ 限时模式" if timed else "🎮 单人模式"
        msg = f"🎯 WordMaster - Wordle 游戏开始！\n"
        msg += f"{mode_text}\n"
        msg += f"📚 词典: {dictionary} | 🔤 长度: {length}\n"
        if timed:
            msg += f"⏰ 限时: 5分钟\n"
        msg += f"🎯 你有 {game.max_attempts} 次机会\n\n"
        msg += "💡 提示:\n"
        msg += "🟩 绿色=正确 | 🟨 黄色=位置错 | ⬜ 灰色=不存在\n\n"
        msg += "发送你的猜测（如: apple）"
        
        yield event.plain_result(msg)
    
    @filter.command("handle")
    async def cmd_handle(self, event: AstrMessageEvent, strict: bool = False, timed: bool = False):
        """开始 Handle 游戏"""
        session_id = self._get_session_id(event)
        user_id = self._get_user_id(event)
        
        if session_id in self.games and self.games[session_id].state == GameState.PLAYING:
            yield event.plain_result("❌ 当前已有游戏在进行中，请先结束当前游戏")
            return
        
        answer = random.choice(self.idiom_list)
        
        game_mode = GameMode.TIMED if timed else GameMode.SINGLE
        time_limit = 300 if timed else 0
        
        game = GameSession(
            game_type=GameType.HANDLE,
            game_mode=game_mode,
            answer=answer,
            host_id=user_id,
            players=[user_id],
            max_attempts=10,
            strict_mode=strict,
            time_limit=time_limit
        )
        self.games[session_id] = game
        
        mode_text = "⏱️ 限时模式" if timed else "🎮 单人模式"
        msg = f"🀄 WordMaster - Handle 猜成语开始！\n"
        msg += f"{mode_text}\n"
        if timed:
            msg += f"⏰ 限时: 5分钟\n"
        msg += f"🎯 你有 {game.max_attempts} 次机会猜出四字成语\n\n"
        msg += "💡 提示:\n"
        msg += "🟩 绿色=正确 | 🟨 黄色=位置错 | ⬜ 灰色=不存在\n"
        msg += "格式: 汉字+声母+韵母+声调\n"
        
        if strict:
            msg += "\n🔒 严格模式已开启\n"
        
        msg += "\n发送你的猜测（如: 一心一意）"
        
        yield event.plain_result(msg)
    
    @filter.command("wordle对战")
    async def cmd_wordle_multi(self, event: AstrMessageEvent, dictionary: str = "CET4", length: int = 5):
        """开始多人对战模式"""
        session_id = self._get_session_id(event)
        user_id = self._get_user_id(event)
        nickname = self._get_nickname(event)
        
        if session_id in self.games and self.games[session_id].state == GameState.PLAYING:
            yield event.plain_result("❌ 当前已有游戏在进行中，请先结束当前游戏")
            return
        
        dictionary = dictionary.upper()
        if dictionary not in self.word_lists:
            available = ", ".join(self.word_lists.keys())
            yield event.plain_result(f"❌ 不支持的词典: {dictionary}\n可用词典: {available}")
            return
        
        word_list = self.word_lists[dictionary]
        valid_words = [w for w in word_list if len(w) == length]
        
        if not valid_words:
            yield event.plain_result(f"❌ 词典 {dictionary} 中没有长度为 {length} 的单词")
            return
        
        answer = random.choice(valid_words)
        
        game = GameSession(
            game_type=GameType.WORDLE,
            game_mode=GameMode.MULTI,
            answer=answer,
            host_id=user_id,
            players=[user_id],
            max_attempts=6
        )
        self.games[session_id] = game
        
        msg = f"⚔️ WordMaster - 多人对战模式！\n"
        msg += f"📚 词典: {dictionary} | 🔤 长度: {length}\n"
        msg += f"👑 房主: {nickname}\n"
        msg += f"🎯 先猜中者获胜！\n\n"
        msg += "其他玩家发送 \"加入\" 即可参与对战\n"
        msg += "房主发送 \"开始\" 开始游戏"
        
        yield event.plain_result(msg)
    
    @filter.event_message()
    async def on_message(self, event: AstrMessageEvent):
        """处理游戏消息"""
        session_id = self._get_session_id(event)
        user_id = self._get_user_id(event)
        nickname = self._get_nickname(event)
        
        if session_id not in self.games:
            return
        
        game = self.games[session_id]
        text = event.message_str.strip()
        
        # 处理加入对战
        if game.game_mode == GameMode.MULTI and game.state == GameState.WAITING:
            if text == "加入":
                if user_id not in game.players:
                    game.players.append(user_id)
                    yield event.plain_result(f"✅ {nickname} 加入对战！当前 {len(game.players)} 人")
                return
            elif text == "开始" and user_id == game.host_id:
                game.state = GameState.PLAYING
                yield event.plain_result(f"🎮 游戏开始！共 {len(game.players)} 人参与\n谁先猜中谁获胜！")
                return
        
        if game.state != GameState.PLAYING:
            return
        
        # 检查是否是对战模式且轮到该玩家
        if game.game_mode == GameMode.MULTI and user_id not in game.players:
            return
        
        # 处理结束命令
        if text in ["结束", "结束游戏", "退出", "quit", "exit"]:
            game.state = GameState.FINISHED
            yield event.plain_result(f"🛑 游戏已结束\n💡 正确答案是: {game.answer}")
            return
        
        # 处理提示命令
        if text in ["提示", "hint", "help"]:
            if game.game_type == GameType.WORDLE:
                hint_msg = f"💡 提示: 单词长度为 {len(game.answer)}\n"
                if game.correct_letters:
                    hint_msg += f"✅ 已确定位置: {game.correct_letters}\n"
                if game.eliminated_letters:
                    hint_msg += f"❌ 已排除字母: {', '.join(sorted(game.eliminated_letters))}"
                yield event.plain_result(hint_msg)
            else:
                yield event.plain_result(f"💡 提示: 这是一个四字成语")
            return
        
        # 处理猜测
        if game.game_type == GameType.WORDLE:
            await self._handle_wordle_guess(event, game, text, user_id, nickname)
        else:
            await self._handle_handle_guess(event, game, text, user_id, nickname)
    
    async def _handle_wordle_guess(self, event: AstrMessageEvent, game: GameSession, guess: str, user_id: str, nickname: str):
        """处理 Wordle 猜测"""
        guess = guess.lower().strip()
        
        if len(guess) != len(game.answer):
            yield event.plain_result(f"❌ 请输入 {len(game.answer)} 个字母的单词")
            return
        
        if not guess.isalpha():
            yield event.plain_result("❌ 只能输入英文字母")
            return
        
        # 记录猜测
        game.guesses.append((user_id, guess))
        
        # 更新提示系统
        result = self._check_wordle_guess(guess, game.answer)
        self._update_hint_system(game, guess, result)
        
        # 格式化输出
        results = [self._check_wordle_guess(g, game.answer) for _, g in game.guesses]
        result_text = self._format_wordle_result(results)
        
        # 检查游戏状态
        if guess == game.answer:
            game.state = GameState.FINISHED
            game.winner = user_id
            game_time = time.time() - game.start_time
            
            # 更新统计
            stats = self._get_or_create_stats(user_id, nickname)
            stats.update_win(len(game.guesses), game_time)
            self._save_stats()
            
            win_text = f"🎉 {nickname} 猜对了！\n" if game.game_mode == GameMode.MULTI else "🎉 恭喜你猜对了！\n"
            msg = f"{result_text}\n\n{win_text}"
            msg += f"🏆 用了 {len(game.guesses)}/{game.max_attempts} 次机会\n"
            msg += f"⏱️ 用时: {int(game_time)}秒"
            
        elif len(game.guesses) >= game.max_attempts:
            game.state = GameState.FINISHED
            game_time = time.time() - game.start_time
            
            # 更新统计
            stats = self._get_or_create_stats(user_id, nickname)
            stats.update_loss(len(game.guesses), game_time)
            self._save_stats()
            
            msg = f"{result_text}\n\n😢 游戏结束，次数用尽\n"
            msg += f"💡 正确答案是: {game.answer.upper()}"
            
        else:
            remaining = game.max_attempts - len(game.guesses)
            msg = f"{result_text}\n\n📝 第 {len(game.guesses)}/{game.max_attempts} 次\n"
            msg += f"💭 还剩 {remaining} 次机会"
            
            # 限时模式显示剩余时间
            if game.time_limit > 0:
                remaining_time = game.get_remaining_time()
                msg += f" | ⏰ {remaining_time}秒"
        
        yield event.plain_result(msg)
    
    async def _handle_handle_guess(self, event: AstrMessageEvent, game: GameSession, guess: str, user_id: str, nickname: str):
        """处理 Handle 猜测"""
        guess = guess.strip()
        
        if len(guess) != 4:
            yield event.plain_result("❌ 请输入四个汉字")
            return
        
        if game.strict_mode and guess not in self.idiom_list:
            yield event.plain_result("❌ 严格模式下，猜测必须是有效的四字成语")
            return
        
        game.guesses.append((user_id, guess))
        
        results = [self._check_handle_guess(g, game.answer) for _, g in game.guesses]
        result_text = self._format_handle_result(results)
        
        if guess == game.answer:
            game.state = GameState.FINISHED
            game.winner = user_id
            game_time = time.time() - game.start_time
            
            stats = self._get_or_create_stats(user_id, nickname)
            stats.update_win(len(game.guesses), game_time)
            self._save_stats()
            
            # 显示成语释义
            meaning = self.idiom_meanings.get(guess, "暂无释义")
            
            win_text = f"🎉 {nickname} 猜对了！\n" if game.game_mode == GameMode.MULTI else "🎉 恭喜你猜对了！\n"
            msg = f"{result_text}\n\n{win_text}"
            msg += f"🏆 用了 {len(game.guesses)}/{game.max_attempts} 次机会\n"
            msg += f"⏱️ 用时: {int(game_time)}秒\n\n"
            msg += f"📖 {guess}: {meaning}"
            
        elif len(game.guesses) >= game.max_attempts:
            game.state = GameState.FINISHED
            game_time = time.time() - game.start_time
            
            stats = self._get_or_create_stats(user_id, nickname)
            stats.update_loss(len(game.guesses), game_time)
            self._save_stats()
            
            meaning = self.idiom_meanings.get(game.answer, "暂无释义")
            
            msg = f"{result_text}\n\n😢 游戏结束，次数用尽\n"
            msg += f"💡 正确答案是: {game.answer}\n"
            msg += f"📖 {game.answer}: {meaning}"
            
        else:
            remaining = game.max_attempts - len(game.guesses)
            msg = f"{result_text}\n\n📝 第 {len(game.guesses)}/{game.max_attempts} 次\n"
            msg += f"💭 还剩 {remaining} 次机会"
            
            if game.time_limit > 0:
                remaining_time = game.get_remaining_time()
                msg += f" | ⏰ {remaining_time}秒"
        
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
    
    @filter.command("我的战绩")
    async def cmd_my_stats(self, event: AstrMessageEvent):
        """查看个人战绩"""
        user_id = self._get_user_id(event)
        nickname = self._get_nickname(event)
        
        stats = self._get_or_create_stats(user_id, nickname)
        
        if stats.total_games == 0:
            yield event.plain_result("📊 你还没有游戏记录，快来开始一局吧！")
            return
        
        win_rate = (stats.wins / stats.total_games * 100) if stats.total_games > 0 else 0
        
        msg = f"📊 {nickname} 的游戏战绩\n"
        msg += "=" * 30 + "\n"
        msg += f"🎮 总游戏数: {stats.total_games}\n"
        msg += f"🏆 获胜: {stats.wins} | 😢 失败: {stats.losses}\n"
        msg += f"📈 胜率: {win_rate:.1f}%\n"
        msg += f"🔥 当前连胜: {stats.win_streak}\n"
        msg += f"🏅 最高连胜: {stats.max_win_streak}\n"
        msg += f"🎯 最佳猜测: {stats.best_attempts if stats.best_attempts < 999 else 'N/A'} 次\n"
        msg += f"📊 平均猜测: {stats.total_attempts/stats.wins:.1f} 次" if stats.wins > 0 else "📊 平均猜测: N/A"
        
        yield event.plain_result(msg)
    
    @filter.command("排行榜")
    async def cmd_leaderboard(self, event: AstrMessageEvent):
        """查看排行榜"""
        if not self.player_stats:
            yield event.plain_result("📊 暂无排行榜数据，快来成为第一个上榜的玩家吧！")
            return
        
        # 按胜场排序
        sorted_players = sorted(
            self.player_stats.values(),
            key=lambda x: (x.wins, x.win_rate if hasattr(x, 'win_rate') else x.wins/max(x.total_games, 1)),
            reverse=True
        )[:10]  # 前10名
        
        msg = "🏆 WordMaster 排行榜 - TOP 10\n"
        msg += "=" * 40 + "\n"
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, player in enumerate(sorted_players):
            win_rate = (player.wins / player.total_games * 100) if player.total_games > 0 else 0
            medal = medals[i] if i < len(medals) else f"{i+1}."
            msg += f"{medal} {player.nickname or player.user_id[:8]}\n"
            msg += f"   🏆 {player.wins}胜 | 📈 {win_rate:.1f}% | 🔥 {player.max_win_streak}连胜\n"
        
        yield event.plain_result(msg)
    
    @filter.command("wordle帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        msg = """🎮 WordMaster - 词汇大师 游戏帮助

📋 游戏命令:
/wordle [词典] [长度] [--timed] - 单人猜单词
  示例: /wordle CET4 5
  示例: /wordle CET6 6 --timed (限时模式)

/handle [--strict] [--timed] - 单人猜成语
  示例: /handle
  示例: /handle --strict --timed

/wordle对战 [词典] [长度] - 多人对战
  示例: /wordle对战 CET4 5

📊 统计命令:
/我的战绩 - 查看个人战绩
/排行榜 - 查看全服排行榜

⚙️ 其他命令:
/结束游戏 - 结束当前游戏
/wordle帮助 - 显示本帮助

🎮 游戏规则:

【Wordle - 英文猜单词】
- 猜一个指定长度的英文单词
- 🟩 绿色=字母正确且位置正确
- 🟨 黄色=字母存在但位置错误
- ⬜ 灰色=字母不存在
- 共6次机会

【Handle - 汉字猜成语】
- 猜一个四字成语
- 显示: 汉字+声母+韵母+声调
- 共10次机会

【限时模式】
- 增加5分钟时间限制
- 超时自动结束

【多人对战】
- 先猜中者获胜
- 其他玩家发送"加入"参与
- 房主发送"开始"开始游戏

💡 游戏中指令:
- "提示" - 获取提示
- "结束" - 结束游戏"""
        
        yield event.plain_result(msg)
