"""
WordMaster - 词汇大师游戏插件
移植自 nonebot-plugin-wordle 和 nonebot-plugin-handle
版本: 2.0.0
功能: Wordle英文猜单词 + Handle汉字猜成语（多人限时对战模式）

使用开源库:
- NLTK: 英文单词库
- pypinyin: 汉字转拼音
- chinese-xinhua: 成语数据库 (GitHub: pwxcoo/chinese-xinhua)
"""

import asyncio
import random
import time
import json
import os
import re
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict
from enum import Enum

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register, StarTools
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
    wordle_wins: int = 0          # Wordle胜场
    wordle_games: int = 0         # Wordle总局数
    handle_wins: int = 0          # Handle胜场
    handle_games: int = 0         # Handle总局数
    first_blood: int = 0          # 首杀次数（第一个猜中）
    fastest_win: float = 99999    # 最快获胜时间
    
    @property
    def win_rate(self) -> float:
        """计算胜率"""
        return (self.wins / self.total_games * 100) if self.total_games > 0 else 0.0
    
    @property
    def wordle_win_rate(self) -> float:
        """Wordle胜率"""
        return (self.wordle_wins / self.wordle_games * 100) if self.wordle_games > 0 else 0.0
    
    @property
    def handle_win_rate(self) -> float:
        """Handle胜率"""
        return (self.handle_wins / self.handle_games * 100) if self.handle_games > 0 else 0.0
    
    def update_win(self, attempts: int, game_time: float, game_type: GameType, is_first: bool = False):
        """更新胜利统计"""
        self.total_games += 1
        self.wins += 1
        self.win_streak += 1
        self.max_win_streak = max(self.max_win_streak, self.win_streak)
        self.best_attempts = min(self.best_attempts, attempts)
        self.total_attempts += attempts
        self.total_time += game_time
        self.avg_time = self.total_time / self.wins if self.wins > 0 else 0
        self.fastest_win = min(self.fastest_win, game_time)
        
        if game_type == GameType.WORDLE:
            self.wordle_games += 1
            self.wordle_wins += 1
        else:
            self.handle_games += 1
            self.handle_wins += 1
        
        if is_first:
            self.first_blood += 1
    
    def update_loss(self, attempts: int, game_time: float, game_type: GameType):
        """更新失败统计"""
        self.total_games += 1
        self.losses += 1
        self.win_streak = 0
        self.total_attempts += attempts
        self.total_time += game_time
        
        if game_type == GameType.WORDLE:
            self.wordle_games += 1
        else:
            self.handle_games += 1


@dataclass
class GameSession:
    """游戏会话"""
    game_type: GameType
    answer: str
    host_id: str                    # 房主ID
    players: List[str] = field(default_factory=list)  # 参与玩家列表
    player_names: Dict[str, str] = field(default_factory=dict)  # 玩家ID到昵称映射
    guesses: List[Tuple[str, str, str]] = field(default_factory=list)  # (玩家ID, 玩家昵称, 猜测)
    state: GameState = GameState.WAITING
    max_attempts: int = 6
    strict_mode: bool = False
    time_limit: int = 300           # 限时5分钟
    start_time: float = field(default_factory=time.time)
    used_letters: Set[str] = field(default_factory=set)  # 已使用的字母
    eliminated_letters: Set[str] = field(default_factory=set)  # 已排除的字母
    correct_letters: Dict[int, str] = field(default_factory=dict)  # 位置正确的字母
    winner: Optional[str] = None    # 获胜者
    winner_name: Optional[str] = None  # 获胜者名称
    word_length: int = 5            # 单词长度
    
    def is_finished(self) -> bool:
        """检查游戏是否结束"""
        if self.state == GameState.FINISHED:
            return True
        if self.winner:
            return True
        if len(self.guesses) >= self.max_attempts:
            return True
        if time.time() - self.start_time > self.time_limit:
            return True
        return False
    
    def get_remaining_time(self) -> int:
        """获取剩余时间"""
        remaining = self.time_limit - (time.time() - self.start_time)
        return max(0, int(remaining))
    
    def get_player_count(self) -> int:
        """获取玩家数量"""
        return len(self.players)
    
    def get_guess_count_by_player(self, user_id: str) -> int:
        """获取某玩家的猜测次数"""
        return sum(1 for uid, _, _ in self.guesses if uid == user_id)


@register("astrbot_plugin_WordMaster", "NumInvis", "WordMaster - 猜词小游戏集合", "2.0.0")
class WordMasterPlugin(Star):
    """WordMaster 词汇大师游戏插件
    
    使用开源库:
    - NLTK: 英文单词库 (nltk.corpus.words)
    - pypinyin: 汉字转拼音 (https://github.com/mozillazg/python-pinyin)
    - chinese-xinhua: 成语数据库 (https://github.com/pwxcoo/chinese-xinhua)
    """
    
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        
        # 游戏会话管理 {session_id: GameSession}
        self.games: Dict[str, GameSession] = {}
        
        # 玩家统计 {user_id: PlayerStats}
        self.player_stats: Dict[str, PlayerStats] = {}
        
        # 数据目录
        self.data_dir = StarTools.get_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 数据缓存
        self.word_list: List[str] = []           # 英文单词列表
        self.idiom_data: Dict[str, Dict] = {}    # 成语数据 {成语: {pinyin, meaning}}
        
        # 初始化
        self._init_libraries()
        self._load_stats()
        
        logger.info(f"WordMaster 词汇大师插件已初始化 v2.0.0")
        logger.info(f"  - 单词库: {len(self.word_list)} 个")
        logger.info(f"  - 成语库: {len(self.idiom_data)} 个")
    
    def _init_libraries(self):
        """初始化开源库"""
        self._init_nltk_words()
        self._init_idioms()
    
    def _init_nltk_words(self):
        """使用NLTK初始化英文单词库"""
        try:
            from nltk.corpus import words
            import nltk
            
            # 尝试下载words数据包
            try:
                nltk.data.find('corpora/words')
            except LookupError:
                logger.info("正在下载NLTK words数据包...")
                nltk.download('words', quiet=True)
            
            # 获取所有英文单词
            all_words = words.words()
            
            # 过滤5-8字母的单词（只保留纯字母）
            self.word_list = [
                w.lower() for w in all_words 
                if 5 <= len(w) <= 8 and w.isalpha() and w.isascii()
            ]
            
            # 去重
            self.word_list = list(set(self.word_list))
            
            logger.info(f"NLTK单词库加载完成，共 {len(self.word_list)} 个单词")
            
        except ImportError:
            logger.warning("NLTK未安装，尝试安装...")
            self._install_and_retry_nltk()
        except Exception as e:
            logger.error(f"加载NLTK单词库失败: {e}")
            self._use_fallback_words()
    
    def _install_and_retry_nltk(self):
        """安装NLTK并重试"""
        try:
            import subprocess
            import sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "nltk", "-q"])
            
            # 重新导入
            from nltk.corpus import words
            import nltk
            nltk.download('words', quiet=True)
            
            all_words = words.words()
            self.word_list = [
                w.lower() for w in all_words 
                if 5 <= len(w) <= 8 and w.isalpha() and w.isascii()
            ]
            self.word_list = list(set(self.word_list))
            logger.info(f"NLTK安装并加载完成，共 {len(self.word_list)} 个单词")
        except Exception as e:
            logger.error(f"安装NLTK失败: {e}")
            self._use_fallback_words()
    
    def _use_fallback_words(self):
        """使用备用单词（从在线API获取或最小集合）"""
        logger.warning("使用备用单词获取方式...")
        
        # 尝试从wordfreq库获取
        try:
            from wordfreq import top_n_list
            words = top_n_list("en", n_top=10000)
            self.word_list = [
                w for w in words 
                if 5 <= len(w) <= 8 and w.isalpha() and w.isascii()
            ]
            logger.info(f"wordfreq单词库加载完成，共 {len(self.word_list)} 个单词")
            return
        except ImportError:
            pass
        
        # 最后的备用：从文件加载或报错
        word_file = self.data_dir / "words_backup.json"
        if word_file.exists():
            try:
                with open(word_file, "r", encoding="utf-8") as f:
                    self.word_list = json.load(f)
                logger.info(f"从备份文件加载 {len(self.word_list)} 个单词")
                return
            except Exception:
                pass
        
        # 如果都失败了，记录错误
        logger.error("无法加载任何单词库，Wordle功能将不可用")
        self.word_list = []
    
    def _init_idioms(self):
        """初始化成语库 - 从chinese-xinhua下载"""
        idiom_file = self.data_dir / "idioms.json"
        
        # 检查是否需要下载
        need_download = False
        if not idiom_file.exists():
            need_download = True
            logger.info("成语库不存在，需要下载...")
        else:
            # 检查文件大小（如果太小可能不完整）
            file_size = idiom_file.stat().st_size
            if file_size < 1000000:  # 小于1MB认为不完整
                need_download = True
                logger.info("成语库文件不完整，重新下载...")
        
        if need_download:
            self._download_idioms()
        
        # 加载成语数据
        self._load_idioms_from_file()
    
    def _download_idioms(self):
        """从chinese-xinhua下载成语数据"""
        import urllib.request
        import ssl
        
        idiom_file = self.data_dir / "idioms.json"
        url = "https://raw.githubusercontent.com/pwxcoo/chinese-xinhua/master/data/idiom.json"
        
        try:
            logger.info("正在从 chinese-xinhua 下载成语数据库...")
            
            # 创建SSL上下文（处理某些环境的证书问题）
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # 下载文件
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0'
            }
            request = urllib.request.Request(url, headers=headers)
            
            with urllib.request.urlopen(request, context=ssl_context, timeout=30) as response:
                data = response.read().decode('utf-8')
                
                # 验证JSON格式
                idioms_raw = json.loads(data)
                
                # 保存到文件
                with open(idiom_file, "w", encoding="utf-8") as f:
                    json.dump(idioms_raw, f, ensure_ascii=False, indent=2)
                
                logger.info(f"成语数据库下载完成，共 {len(idioms_raw)} 个成语")
                
        except Exception as e:
            logger.error(f"下载成语数据库失败: {e}")
            # 创建空文件标记
            if not idiom_file.exists():
                with open(idiom_file, "w", encoding="utf-8") as f:
                    json.dump([], f)
    
    def _load_idioms_from_file(self):
        """从文件加载成语数据"""
        idiom_file = self.data_dir / "idioms.json"
        
        if not idiom_file.exists():
            logger.warning("成语库文件不存在")
            return
        
        try:
            with open(idiom_file, "r", encoding="utf-8") as f:
                idioms_raw = json.load(f)
            
            # 转换为内部格式 {成语: {pinyin, meaning}}
            self.idiom_data = {}
            for item in idioms_raw:
                if isinstance(item, dict) and "word" in item:
                    word = item["word"]
                    # 只保留四字成语
                    if len(word) == 4:
                        self.idiom_data[word] = {
                            "pinyin": item.get("pinyin", ""),
                            "meaning": item.get("explanation", ""),
                            "derivation": item.get("derivation", ""),
                            "example": item.get("example", "")
                        }
            
            logger.info(f"成语库加载完成，共 {len(self.idiom_data)} 个四字成语")
            
        except Exception as e:
            logger.error(f"加载成语库失败: {e}")
            self.idiom_data = {}
    
    def _get_pinyin_with_pypinyin(self, char: str) -> str:
        """使用pypinyin库获取汉字拼音"""
        try:
            from pypinyin import pinyin, Style
            
            result = pinyin(char, style=Style.TONE3, heteronym=False)
            if result and len(result) > 0:
                return result[0][0]  # 返回第一个读音
            return char
        except ImportError:
            logger.warning("pypinyin未安装，尝试安装...")
            self._install_pypinyin()
            return self._get_pinyin_with_pypinyin(char)
        except Exception as e:
            logger.error(f"获取拼音失败: {e}")
            return char
    
    def _install_pypinyin(self):
        """安装pypinyin库"""
        try:
            import subprocess
            import sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pypinyin", "-q"])
            logger.info("pypinyin安装完成")
        except Exception as e:
            logger.error(f"安装pypinyin失败: {e}")
    
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
        elif nickname and not self.player_stats[user_id].nickname:
            self.player_stats[user_id].nickname = nickname
        return self.player_stats[user_id]
    
    def _save_stats(self):
        """保存玩家统计到文件"""
        try:
            stats_file = self.data_dir / "wordmaster_stats.json"
            data = {uid: asdict(stats) for uid, stats in self.player_stats.items()}
            with open(stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存统计失败: {e}")
    
    def _load_stats(self):
        """从文件加载玩家统计"""
        try:
            stats_file = self.data_dir / "wordmaster_stats.json"
            if stats_file.exists():
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
    
    def _get_random_word(self, length: int = 5) -> Optional[str]:
        """获取随机单词"""
        valid_words = [w for w in self.word_list if len(w) == length]
        if valid_words:
            return random.choice(valid_words)
        return None
    
    def _check_wordle_guess(self, guess: str, answer: str) -> List[Tuple[str, str]]:
        """检查 Wordle 猜测结果"""
        result = []
        answer_chars = list(answer)
        guess_chars = list(guess)
        
        # 第一轮：标记完全正确的
        for i, (g, a) in enumerate(zip(guess_chars, answer_chars)):
            if g == a:
                result.append((g, "G"))
                answer_chars[i] = None
                guess_chars[i] = None
            else:
                result.append((g, "X"))
        
        # 第二轮：标记位置错误的
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
            game.used_letters.add(char.lower())
            if status == "G":
                game.correct_letters[i] = char
            elif status == "X":
                game.eliminated_letters.add(char.lower())
    
    def _get_wordle_hint(self, game: GameSession) -> str:
        """获取 Wordle 提示"""
        hint_parts = []
        
        # 显示已确定的位置
        if game.correct_letters:
            positions = []
            for pos, char in sorted(game.correct_letters.items()):
                positions.append(f"第{pos+1}位={char.upper()}")
            hint_parts.append(f"✅ 已确定: {', '.join(positions)}")
        
        # 显示已排除的字母
        if game.eliminated_letters:
            eliminated = sorted(game.eliminated_letters)
            hint_parts.append(f"❌ 已排除: {', '.join(eliminated).upper()}")
        
        # 显示已使用但未排除的字母
        present_letters = game.used_letters - game.eliminated_letters - set(game.correct_letters.values())
        if present_letters:
            hint_parts.append(f"💡 存在但位置不对: {', '.join(sorted(present_letters)).upper()}")
        
        return "\n".join(hint_parts) if hint_parts else "暂无有效提示"
    
    # ========== Handle 游戏逻辑 ==========
    
    def _get_random_idiom(self) -> Optional[Tuple[str, Dict]]:
        """获取随机成语"""
        if self.idiom_data:
            idiom = random.choice(list(self.idiom_data.keys()))
            return idiom, self.idiom_data[idiom]
        return None
    
    def _get_pinyin(self, char: str) -> str:
        """获取汉字拼音 - 优先使用pypinyin"""
        # 尝试使用pypinyin
        try:
            from pypinyin import pinyin, Style
            result = pinyin(char, style=Style.TONE3, heteronym=False)
            if result and len(result) > 0:
                return result[0][0]
        except:
            pass
        
        # 从成语数据中提取拼音作为后备
        for idiom, data in self.idiom_data.items():
            if char in idiom and "pinyin" in data:
                pinyin_str = data["pinyin"]
                parts = pinyin_str.split()
                for i, c in enumerate(idiom):
                    if c == char and i < len(parts):
                        return parts[i]
        
        return char
    
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
            
            # 汉字
            if g_char == a_char:
                char_result.append((g_char, "G"))
            elif g_char in answer_chars:
                char_result.append((g_char, "Y"))
            else:
                char_result.append((g_char, "X"))
            
            # 声母
            if g_sheng == a_sheng:
                char_result.append((g_sheng, "G"))
            elif g_sheng and any(self._parse_pinyin(self._get_pinyin(c))[0] == g_sheng for c in answer_chars):
                char_result.append((g_sheng, "Y"))
            else:
                char_result.append((g_sheng if g_sheng else "-", "X"))
            
            # 韵母
            if g_yun == a_yun:
                char_result.append((g_yun, "G"))
            elif g_yun and any(self._parse_pinyin(self._get_pinyin(c))[1] == g_yun for c in answer_chars):
                char_result.append((g_yun, "Y"))
            else:
                char_result.append((g_yun if g_yun else "-", "X"))
            
            # 声调
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
    
    def _get_handle_hint(self, game: GameSession) -> str:
        """获取 Handle 提示"""
        answer = game.answer
        hints = []
        
        # 随机显示一个字的拼音首字母
        random_pos = random.randint(0, 3)
        char = answer[random_pos]
        pinyin = self._get_pinyin(char)
        sheng = self._parse_pinyin(pinyin)[0]
        
        hints.append(f"💡 第{random_pos+1}个字拼音首字母: {sheng}")
        
        # 如果猜测次数超过5次，再给更多提示
        if len(game.guesses) >= 5:
            data = self.idiom_data.get(answer, {})
            meaning = data.get("meaning", "")
            if meaning:
                hints.append(f"📖 释义提示: {meaning[:10]}...")
        
        return "\n".join(hints)
    
    # ========== 命令处理 ==========
    
    @filter.command("wordle")
    async def cmd_wordle(self, event: AstrMessageEvent, length: int = 5):
        """开始 Wordle 多人限时游戏
        
        Args:
            length: 单词长度 (5-8)
        """
        session_id = self._get_session_id(event)
        user_id = self._get_user_id(event)
        nickname = self._get_nickname(event)
        
        if session_id in self.games and self.games[session_id].state != GameState.FINISHED:
            yield event.plain_result("❌ 当前已有游戏在进行中，请先结束当前游戏")
            return
        
        # 验证长度
        if not 5 <= length <= 8:
            yield event.plain_result("❌ 单词长度必须在 5-8 之间")
            return
        
        # 检查单词库
        if not self.word_list:
            yield event.plain_result("❌ 单词库未加载，请检查NLTK安装")
            return
        
        # 获取随机单词
        answer = self._get_random_word(length)
        if not answer:
            yield event.plain_result(f"❌ 没有找到长度为 {length} 的单词")
            return
        
        game = GameSession(
            game_type=GameType.WORDLE,
            answer=answer,
            host_id=user_id,
            players=[user_id],
            player_names={user_id: nickname},
            max_attempts=6,
            time_limit=300,
            word_length=length
        )
        self.games[session_id] = game
        
        msg = f"⚔️ WordMaster - Wordle 多人限时对战！\n"
        msg += f"🔤 单词长度: {length}\n"
        msg += f"👑 房主: {nickname}\n"
        msg += f"⏰ 限时: 5分钟 | 🎯 次数: 6次\n"
        msg += f"🎮 先猜中者获胜！\n\n"
        msg += "其他玩家发送 \"加入\" 即可参与对战\n"
        msg += "房主发送 \"开始\" 开始游戏"
        
        yield event.plain_result(msg)
    
    @filter.command("handle")
    async def cmd_handle(self, event: AstrMessageEvent, strict: bool = False):
        """开始 Handle 多人限时游戏"""
        session_id = self._get_session_id(event)
        user_id = self._get_user_id(event)
        nickname = self._get_nickname(event)
        
        if session_id in self.games and self.games[session_id].state != GameState.FINISHED:
            yield event.plain_result("❌ 当前已有游戏在进行中，请先结束当前游戏")
            return
        
        # 检查成语库
        if not self.idiom_data:
            yield event.plain_result("❌ 成语库未加载，请稍后重试")
            return
        
        # 获取随机成语
        result = self._get_random_idiom()
        if not result:
            yield event.plain_result("❌ 成语库为空")
            return
        
        answer, data = result
        
        game = GameSession(
            game_type=GameType.HANDLE,
            answer=answer,
            host_id=user_id,
            players=[user_id],
            player_names={user_id: nickname},
            max_attempts=10,
            strict_mode=strict,
            time_limit=300
        )
        self.games[session_id] = game
        
        msg = f"⚔️ WordMaster - Handle 多人限时对战！\n"
        msg += f"👑 房主: {nickname}\n"
        msg += f"⏰ 限时: 5分钟 | 🎯 次数: 10次\n"
        msg += f"🎮 先猜中者获胜！\n\n"
        msg += "其他玩家发送 \"加入\" 即可参与对战\n"
        msg += "房主发送 \"开始\" 开始游戏"
        
        if strict:
            msg += "\n🔒 严格模式已开启（必须是有效成语）"
        
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
        if game.state == GameState.WAITING:
            if text == "加入":
                if user_id not in game.players:
                    game.players.append(user_id)
                    game.player_names[user_id] = nickname
                    yield event.plain_result(f"✅ {nickname} 加入对战！当前 {len(game.players)} 人")
                else:
                    yield event.plain_result(f"⚠️ {nickname} 已经在房间中了")
                return
            elif text == "开始" and user_id == game.host_id:
                if len(game.players) < 1:
                    yield event.plain_result("❌ 至少需要1名玩家才能开始")
                    return
                game.state = GameState.PLAYING
                game.start_time = time.time()
                players_list = ", ".join([game.player_names.get(pid, f"玩家{pid}") for pid in game.players])
                yield event.plain_result(f"🎮 游戏开始！\n👥 参与者: {players_list}\n⏰ 限时5分钟，谁先猜中谁获胜！")
                return
        
        if game.state != GameState.PLAYING:
            return
        
        # 检查是否是房间内玩家
        if user_id not in game.players:
            return
        
        # 处理结束命令
        if text in ["结束", "结束游戏", "退出", "quit", "exit"]:
            if user_id == game.host_id:
                game.state = GameState.FINISHED
                if game.game_type == GameType.WORDLE:
                    yield event.plain_result(f"🛑 游戏已结束\n💡 正确答案是: {game.answer.upper()}")
                else:
                    data = self.idiom_data.get(game.answer, {})
                    meaning = data.get("meaning", "暂无释义")
                    yield event.plain_result(f"🛑 游戏已结束\n💡 正确答案是: {game.answer}\n📖 {meaning}")
            else:
                yield event.plain_result("❌ 只有房主可以结束游戏")
            return
        
        # 处理提示命令
        if text in ["提示", "hint", "help"]:
            if game.game_type == GameType.WORDLE:
                hint_msg = f"💡 提示: 单词长度为 {len(game.answer)}\n"
                hint_msg += self._get_wordle_hint(game)
                yield event.plain_result(hint_msg)
            else:
                hint_msg = f"💡 提示: 这是一个四字成语\n"
                hint_msg += self._get_handle_hint(game)
                yield event.plain_result(hint_msg)
            return
        
        # 处理猜测
        if game.game_type == GameType.WORDLE:
            async for result in self._handle_wordle_guess(event, game, text, user_id, nickname):
                yield result
        else:
            async for result in self._handle_handle_guess(event, game, text, user_id, nickname):
                yield result
    
    async def _handle_wordle_guess(self, event: AstrMessageEvent, game: GameSession, guess: str, user_id: str, nickname: str):
        """处理 Wordle 猜测"""
        guess = guess.lower().strip()
        
        if len(guess) != len(game.answer):
            yield event.plain_result(f"❌ 请输入 {len(game.answer)} 个字母的单词")
            return
        
        if not guess.isalpha():
            yield event.plain_result("❌ 只能输入英文字母")
            return
        
        # 检查是否超时
        remaining_time = game.get_remaining_time()
        if remaining_time <= 0:
            game.state = GameState.FINISHED
            yield event.plain_result(f"⏰ 时间到！游戏结束\n💡 正确答案是: {game.answer.upper()}")
            return
        
        # 记录猜测
        game.guesses.append((user_id, nickname, guess))
        
        # 更新提示系统
        result = self._check_wordle_guess(guess, game.answer)
        self._update_hint_system(game, guess, result)
        
        # 格式化输出
        results = [self._check_wordle_guess(g, game.answer) for _, _, g in game.guesses]
        result_text = self._format_wordle_result(results)
        
        # 检查游戏状态
        if guess == game.answer:
            game.state = GameState.FINISHED
            game.winner = user_id
            game.winner_name = nickname
            game_time = time.time() - game.start_time
            
            # 更新统计
            stats = self._get_or_create_stats(user_id, nickname)
            stats.update_win(len([g for uid, _, g in game.guesses if uid == user_id]), game_time, GameType.WORDLE, is_first=True)
            self._save_stats()
            
            msg = f"{result_text}\n\n🎉 {nickname} 猜对了！\n"
            msg += f"🏆 用了 {len([g for uid, _, g in game.guesses if uid == user_id])} 次机会\n"
            msg += f"⏱️ 用时: {int(game_time)}秒"
            
        elif len(game.guesses) >= game.max_attempts:
            game.state = GameState.FINISHED
            game_time = time.time() - game.start_time
            
            # 更新所有玩家的统计
            for pid in game.players:
                pname = game.player_names.get(pid, f"玩家{pid}")
                stats = self._get_or_create_stats(pid, pname)
                stats.update_loss(game.get_guess_count_by_player(pid), game_time, GameType.WORDLE)
            self._save_stats()
            
            msg = f"{result_text}\n\n😢 游戏结束，次数用尽\n"
            msg += f"💡 正确答案是: {game.answer.upper()}"
            
        else:
            remaining = game.max_attempts - len(game.guesses)
            msg = f"{result_text}\n\n📝 第 {len(game.guesses)}/{game.max_attempts} 次\n"
            msg += f"💭 还剩 {remaining} 次 | ⏰ {remaining_time}秒"
        
        yield event.plain_result(msg)
    
    async def _handle_handle_guess(self, event: AstrMessageEvent, game: GameSession, guess: str, user_id: str, nickname: str):
        """处理 Handle 猜测"""
        guess = guess.strip()
        
        if len(guess) != 4:
            yield event.plain_result("❌ 请输入四个汉字")
            return
        
        # 检查是否超时
        remaining_time = game.get_remaining_time()
        if remaining_time <= 0:
            game.state = GameState.FINISHED
            data = self.idiom_data.get(game.answer, {})
            meaning = data.get("meaning", "暂无释义")
            yield event.plain_result(f"⏰ 时间到！游戏结束\n💡 正确答案是: {game.answer}\n📖 {meaning}")
            return
        
        if game.strict_mode and guess not in self.idiom_data:
            yield event.plain_result("❌ 严格模式下，猜测必须是有效的四字成语")
            return
        
        game.guesses.append((user_id, nickname, guess))
        
        results = [self._check_handle_guess(g, game.answer) for _, _, g in game.guesses]
        result_text = self._format_handle_result(results)
        
        if guess == game.answer:
            game.state = GameState.FINISHED
            game.winner = user_id
            game.winner_name = nickname
            game_time = time.time() - game.start_time
            
            stats = self._get_or_create_stats(user_id, nickname)
            stats.update_win(len([g for uid, _, g in game.guesses if uid == user_id]), game_time, GameType.HANDLE, is_first=True)
            self._save_stats()
            
            data = self.idiom_data.get(guess, {})
            meaning = data.get("meaning", "暂无释义")
            full_pinyin = data.get("pinyin", "")
            
            msg = f"{result_text}\n\n🎉 {nickname} 猜对了！\n"
            msg += f"🏆 用了 {len([g for uid, _, g in game.guesses if uid == user_id])} 次机会\n"
            msg += f"⏱️ 用时: {int(game_time)}秒\n\n"
            if full_pinyin:
                msg += f"🔤 拼音: {full_pinyin}\n"
            msg += f"📖 {guess}: {meaning}"
            
        elif len(game.guesses) >= game.max_attempts:
            game.state = GameState.FINISHED
            game_time = time.time() - game.start_time
            
            # 更新所有玩家的统计
            for pid in game.players:
                pname = game.player_names.get(pid, f"玩家{pid}")
                stats = self._get_or_create_stats(pid, pname)
                stats.update_loss(game.get_guess_count_by_player(pid), game_time, GameType.HANDLE)
            self._save_stats()
            
            data = self.idiom_data.get(game.answer, {})
            meaning = data.get("meaning", "暂无释义")
            full_pinyin = data.get("pinyin", "")
            
            msg = f"{result_text}\n\n😢 游戏结束，次数用尽\n"
            msg += f"💡 正确答案是: {game.answer}\n"
            if full_pinyin:
                msg += f"🔤 拼音: {full_pinyin}\n"
            msg += f"📖 {game.answer}: {meaning}"
            
        else:
            remaining = game.max_attempts - len(game.guesses)
            msg = f"{result_text}\n\n📝 第 {len(game.guesses)}/{game.max_attempts} 次\n"
            msg += f"💭 还剩 {remaining} 次 | ⏰ {remaining_time}秒"
        
        yield event.plain_result(msg)
    
    @filter.command("结束游戏")
    async def cmd_end_game(self, event: AstrMessageEvent):
        """结束当前游戏"""
        session_id = self._get_session_id(event)
        user_id = self._get_user_id(event)
        
        if session_id not in self.games or self.games[session_id].state == GameState.FINISHED:
            yield event.plain_result("❌ 当前没有进行中的游戏")
            return
        
        game = self.games[session_id]
        
        # 只有房主或管理员可以结束游戏
        if user_id != game.host_id and not self._is_admin(event):
            yield event.plain_result("❌ 只有房主或管理员可以结束游戏")
            return
        
        game.state = GameState.FINISHED
        
        if game.game_type == GameType.WORDLE:
            yield event.plain_result(f"🛑 游戏已结束\n💡 正确答案是: {game.answer.upper()}")
        else:
            data = self.idiom_data.get(game.answer, {})
            meaning = data.get("meaning", "暂无释义")
            yield event.plain_result(f"🛑 游戏已结束\n💡 正确答案是: {game.answer}\n📖 {meaning}")
    
    @filter.command("我的战绩")
    async def cmd_my_stats(self, event: AstrMessageEvent):
        """查看个人战绩"""
        user_id = self._get_user_id(event)
        nickname = self._get_nickname(event)
        
        stats = self._get_or_create_stats(user_id, nickname)
        
        if stats.total_games == 0:
            yield event.plain_result("📊 你还没有游戏记录，快来开始一局吧！")
            return
        
        msg = f"📊 {nickname} 的游戏战绩\n"
        msg += "=" * 30 + "\n"
        msg += f"🎮 总游戏数: {stats.total_games}\n"
        msg += f"🏆 获胜: {stats.wins} | 😢 失败: {stats.losses}\n"
        msg += f"📈 胜率: {stats.win_rate:.1f}%\n"
        msg += f"🔥 当前连胜: {stats.win_streak}\n"
        msg += f"🏅 最高连胜: {stats.max_win_streak}\n"
        msg += f"🎯 最佳猜测: {stats.best_attempts if stats.best_attempts < 999 else 'N/A'} 次\n"
        msg += f"⚡ 最快获胜: {int(stats.fastest_win)}秒\n"
        msg += f"🩸 首杀次数: {stats.first_blood}\n"
        msg += "-" * 30 + "\n"
        msg += f"🔤 Wordle: {stats.wordle_wins}/{stats.wordle_games} 胜 ({stats.wordle_win_rate:.1f}%)\n"
        msg += f"🀄 Handle: {stats.handle_wins}/{stats.handle_games} 胜 ({stats.handle_win_rate:.1f}%)\n"
        if stats.wins > 0:
            msg += f"📊 平均猜测: {stats.total_attempts/stats.wins:.1f} 次"
        
        yield event.plain_result(msg)
    
    @filter.command("排行榜")
    async def cmd_leaderboard(self, event: AstrMessageEvent, type: str = "wins"):
        """查看排行榜
        
        Args:
            type: 排序类型 (wins/胜率/首杀/连胜)
        """
        if not self.player_stats:
            yield event.plain_result("📊 暂无排行榜数据，快来成为第一个上榜的玩家吧！")
            return
        
        # 根据类型排序
        if type == "胜率":
            sorted_players = sorted(
                [p for p in self.player_stats.values() if p.total_games >= 5],  # 至少5场
                key=lambda x: x.win_rate,
                reverse=True
            )[:10]
            title = "胜率排行榜"
        elif type == "首杀":
            sorted_players = sorted(
                self.player_stats.values(),
                key=lambda x: x.first_blood,
                reverse=True
            )[:10]
            title = "首杀排行榜"
        elif type == "连胜":
            sorted_players = sorted(
                self.player_stats.values(),
                key=lambda x: x.max_win_streak,
                reverse=True
            )[:10]
            title = "连胜排行榜"
        else:  # wins
            sorted_players = sorted(
                self.player_stats.values(),
                key=lambda x: (x.wins, x.win_rate),
                reverse=True
            )[:10]
            title = "胜场排行榜"
        
        if not sorted_players:
            yield event.plain_result(f"📊 暂无{title}数据")
            return
        
        msg = f"🏆 WordMaster {title} - TOP 10\n"
        msg += "=" * 40 + "\n"
        
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, player in enumerate(sorted_players):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            name = player.nickname or player.user_id[:8]
            
            if type == "胜率":
                msg += f"{medal} {name}\n"
                msg += f"   📈 {player.win_rate:.1f}% | 🎮 {player.total_games}场\n"
            elif type == "首杀":
                msg += f"{medal} {name}\n"
                msg += f"   🩸 {player.first_blood}次 | 🏆 {player.wins}胜\n"
            elif type == "连胜":
                msg += f"{medal} {name}\n"
                msg += f"   🔥 {player.max_win_streak}连胜 | 🏆 {player.wins}胜\n"
            else:
                msg += f"{medal} {name}\n"
                msg += f"   🏆 {player.wins}胜 | 📈 {player.win_rate:.1f}% | 🔥 {player.max_win_streak}连胜\n"
        
        yield event.plain_result(msg)
    
    @filter.command("游戏状态")
    async def cmd_game_status(self, event: AstrMessageEvent):
        """查看当前游戏状态"""
        session_id = self._get_session_id(event)
        
        if session_id not in self.games:
            yield event.plain_result("❌ 当前没有游戏")
            return
        
        game = self.games[session_id]
        
        if game.game_type == GameType.WORDLE:
            msg = "🎮 Wordle 游戏状态\n"
            msg += f"🔤 单词长度: {game.word_length}\n"
        else:
            msg = "🎮 Handle 游戏状态\n"
        
        msg += f"👑 房主: {game.player_names.get(game.host_id, '未知')}\n"
        msg += f"👥 玩家数: {len(game.players)}\n"
        msg += f"📝 已猜测: {len(game.guesses)}/{game.max_attempts}\n"
        
        if game.state == GameState.WAITING:
            msg += "⏳ 状态: 等待开始\n"
            msg += "玩家: " + ", ".join([game.player_names.get(pid, f"玩家{pid}") for pid in game.players])
        elif game.state == GameState.PLAYING:
            remaining = game.get_remaining_time()
            msg += f"▶️ 状态: 进行中\n"
            msg += f"⏰ 剩余时间: {remaining}秒"
        else:
            msg += "⏹️ 状态: 已结束"
            if game.winner_name:
                msg += f"\n🏆 获胜者: {game.winner_name}"
        
        yield event.plain_result(msg)
    
    @filter.command("wordle帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        msg = f"""🎮 WordMaster - 词汇大师 游戏帮助 v2.0.0

📚 开源库依赖:
• NLTK (nltk.corpus.words) - 英文单词库
• pypinyin - 汉字转拼音 (github.com/mozillazg/python-pinyin)
• chinese-xinhua - 成语数据库 (github.com/pwxcoo/chinese-xinhua)

📊 题库信息:
• 单词库: {len(self.word_list)} 个单词 (NLTK)
• 成语库: {len(self.idiom_data)} 个成语 (chinese-xinhua)

📋 游戏命令:
/wordle [长度] - 开始英文猜单词对战
  示例: /wordle 5
  长度: 5-8 (默认5)

/handle [--strict] - 开始汉字猜成语对战
  示例: /handle
  --strict: 开启严格模式（必须是成语）

⚔️ 多人对战:
• 发送 "加入" 参与对战
• 房主发送 "开始" 开始游戏
• 先猜中者获胜！

⏱️ 限时规则:
• 限时5分钟
• 次数用尽或超时结束

📊 统计命令:
/我的战绩 - 查看个人战绩
/排行榜 [类型] - 查看排行榜
  类型: wins(胜场)/胜率/首杀/连胜
/游戏状态 - 查看当前游戏状态

⚙️ 其他命令:
/结束游戏 - 结束当前游戏
/wordle帮助 - 显示本帮助

🎮 游戏规则:

【Wordle - 英文猜单词】
• 猜指定长度的英文单词
• 🟩 绿色=正确 | 🟨 黄色=位置错 | ⬜ 灰色=不存在
• 共6次机会

【Handle - 汉字猜成语】
• 猜四字成语
• 显示: 汉字+声母+韵母+声调
• 🟩 绿色=完全正确
• 🟨 黄色=存在但位置/类型不对
• ⬜ 灰色=不存在
• 共10次机会

💡 游戏中指令:
• "提示" - 获取提示
• "结束" - 结束游戏（仅房主）"""
        
        yield event.plain_result(msg)
