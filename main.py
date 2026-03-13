"""
WordMaster - 词汇大师游戏插件
移植自 nonebot-plugin-wordle 和 nonebot-plugin-handle
版本: 1.2.0
功能: Wordle英文猜单词 + Handle汉字猜成语（多人限时对战模式）
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


@register("astrbot_plugin_WordMaster", "NumInvis", "WordMaster - 猜词小游戏集合", "1.2.0")
class WordMasterPlugin(Star):
    """WordMaster 词汇大师游戏插件"""
    
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
        
        # 加载数据
        self.word_list: List[str] = []
        self.idiom_data: Dict[str, Dict] = {}  # 成语数据 {成语: {pinyin, meaning}}
        self._load_all_data()
        self._load_stats()
        
        logger.info(f"WordMaster 词汇大师插件已初始化 v1.2.0")
        logger.info(f"  - 单词库: {len(self.word_list)} 个")
        logger.info(f"  - 成语库: {len(self.idiom_data)} 个")
    
    def _load_all_data(self):
        """加载所有数据"""
        self._load_word_list()
        self._load_idiom_data()
    
    def _load_word_list(self):
        """加载英文单词列表 - 使用内置常用单词 + 尝试从文件加载"""
        # 内置常用5-8字母单词（约2000个常用词）
        builtin_words = self._get_builtin_words()
        
        # 尝试从数据文件加载
        word_file = self.data_dir / "words.json"
        if word_file.exists():
            try:
                with open(word_file, "r", encoding="utf-8") as f:
                    file_words = json.load(f)
                    if isinstance(file_words, list):
                        self.word_list = list(set(builtin_words + file_words))
                        logger.info(f"从文件加载了 {len(file_words)} 个单词")
                    else:
                        self.word_list = builtin_words
            except Exception as e:
                logger.warning(f"加载单词文件失败: {e}")
                self.word_list = builtin_words
        else:
            self.word_list = builtin_words
            # 保存内置单词到文件
            self._save_word_list()
        
        # 过滤只保留5-8字母的单词
        self.word_list = [w.lower() for w in self.word_list if 5 <= len(w) <= 8 and w.isalpha()]
        self.word_list = list(set(self.word_list))  # 去重
        
        logger.info(f"单词库加载完成，共 {len(self.word_list)} 个单词")
    
    def _get_builtin_words(self) -> List[str]:
        """获取内置常用单词"""
        return [
            # 5字母单词
            "apple", "beach", "chair", "dance", "eagle", "flame", "grape", "house",
            "image", "judge", "knife", "lemon", "music", "night", "ocean", "piano",
            "queen", "radio", "sheep", "table", "uncle", "video", "water", "youth",
            "zebra", "bread", "cloud", "dream", "earth", "fruit", "green", "heart",
            "island", "juice", "light", "money", "north", "orange", "peace", "quiet",
            "river", "smile", "tiger", "unity", "voice", "watch", "young", "alarm",
            "brush", "camel", "dress", "entry", "focus", "glass", "hotel", "input",
            "jelly", "kite", "lunch", "match", "novel", "order", "paint", "quote",
            "rider", "scale", "toast", "urban", "visit", "whale", "yield", "zone",
            "about", "above", "abuse", "actor", "acute", "admit", "adopt", "adult",
            "after", "again", "agent", "agree", "ahead", "alarm", "album", "alert",
            "alike", "alive", "allow", "alone", "along", "alter", "among", "anger",
            "angle", "angry", "apart", "apple", "apply", "arena", "argue", "arise",
            "array", "aside", "asset", "audio", "audit", "avoid", "award", "aware",
            "badly", "baker", "bases", "basic", "basis", "beach", "began", "begin",
            "begun", "being", "below", "bench", "billy", "birth", "black", "blame",
            "blind", "block", "blood", "board", "boost", "booth", "bound", "brain",
            "brand", "bread", "break", "breed", "brief", "bring", "broad", "broke",
            "brown", "build", "built", "buyer", "cable", "calm", "canal", "carry",
            "catch", "cause", "chain", "chair", "chart", "chase", "cheap", "check",
            "chest", "chief", "child", "china", "chose", "civil", "claim", "class",
            "clean", "clear", "click", "clock", "close", "coach", "coast", "could",
            "count", "court", "cover", "craft", "crash", "cream", "crime", "cross",
            "crowd", "crown", "curve", "daily", "dance", "dated", "dealt", "death",
            "debut", "delay", "depth", "doing", "doubt", "dozen", "draft", "drama",
            "drawn", "dream", "dress", "drill", "drink", "drive", "drove", "dying",
            "early", "earth", "eight", "elite", "empty", "enemy", "enjoy", "enter",
            "entry", "equal", "error", "event", "every", "exact", "exist", "extra",
            "faith", "false", "fault", "fiber", "field", "fifth", "fifty", "fight",
            "final", "first", "fixed", "flash", "fleet", "floor", "fluid", "focus",
            "force", "forth", "forty", "forum", "found", "frame", "frank", "fraud",
            "fresh", "front", "fruit", "fully", "funny", "giant", "given", "glass",
            "globe", "going", "grace", "grade", "grand", "grant", "grass", "great",
            "green", "gross", "group", "grown", "guard", "guess", "guest", "guide",
            "happy", "heart", "heavy", "hello", "help", "hence", "horse", "hotel",
            "house", "human", "ideal", "image", "index", "inner", "input", "issue",
            "japan", "joint", "jones", "judge", "known", "label", "large", "laser",
            "later", "laugh", "layer", "learn", "lease", "least", "leave", "legal",
            "level", "light", "limit", "links", "lives", "local", "logic", "loose",
            "lower", "lucky", "lunch", "lying", "magic", "major", "maker", "march",
            "maria", "match", "maybe", "mayor", "meant", "media", "metal", "might",
            "minor", "minus", "mixed", "model", "money", "month", "moral", "motor",
            "mount", "mouse", "mouth", "movie", "music", "needs", "never", "newly",
            "night", "noise", "north", "noted", "novel", "nurse", "occur", "ocean",
            "offer", "often", "order", "other", "ought", "paint", "panel", "paper",
            "party", "peace", "phase", "phone", "photo", "piece", "pilot", "pitch",
            "place", "plain", "plane", "plant", "plate", "point", "pound", "power",
            "press", "price", "pride", "prime", "print", "prior", "prize", "proof",
            "proud", "prove", "queen", "quick", "quiet", "quite", "radio", "raise",
            "range", "rapid", "ratio", "reach", "ready", "refer", "right", "rival",
            "river", "robin", "robot", "round", "route", "royal", "rural", "scale",
            "scene", "scope", "score", "seal", "search", "season", "seat", "second",
            "secret", "section", "sector", "secure", "see", "seed", "seek", "seem",
            "seen", "seize", "select", "sell", "send", "sense", "sent", "sequence",
            "series", "serious", "serve", "service", "session", "set", "setting",
            "settle", "settlement", "seven", "several", "severe", "sex", "sexual",
            "shake", "shall", "shape", "share", "sharp", "she", "sheet", "shelf",
            "shell", "shelter", "shift", "shine", "ship", "shirt", "shock", "shoe",
            "shoot", "shop", "shopping", "shore", "short", "shot", "should", "shoulder",
            "shout", "show", "shower", "shrug", "shut", "sick", "side", "sigh",
            "sight", "sign", "signal", "signature", "significance", "significant",
            "silence", "silent", "silver", "similar", "similarly", "simple", "simply",
            "sin", "since", "sing", "singer", "single", "sink", "sir", "sister",
            "sit", "site", "situation", "six", "size", "ski", "skill", "skin",
            "sky", "slave", "sleep", "slice", "slide", "slight", "slightly", "slip",
            "slow", "slowly", "small", "smart", "smell", "smile", "smoke", "smooth",
            "snap", "snow", "so", "soccer", "social", "society", "soft", "software",
            "soil", "solar", "soldier", "solid", "solution", "solve", "some", "somebody",
            "somehow", "someone", "something", "sometimes", "somewhat", "somewhere",
            "son", "song", "soon", "sophisticated", "sorry", "sort", "soul", "sound",
            "soup", "source", "south", "southern", "space", "spanish", "speak", "speaker",
            "special", "specialist", "species", "specific", "specifically", "specify",
            "speech", "speed", "spend", "spending", "spin", "spirit", "spiritual",
            "split", "spokesman", "sport", "spot", "spread", "spring", "square",
            "squeeze", "stability", "stable", "staff", "stage", "stair", "stake",
            "stand", "standard", "standing", "star", "stare", "start", "state",
            "statement", "station", "statistics", "status", "stay", "steady", "steal",
            "steel", "step", "stick", "still", "stir", "stock", "stomach", "stone",
            "stop", "storage", "store", "storm", "story", "straight", "strange",
            "stranger", "strategic", "strategy", "stream", "street", "strength",
            "strengthen", "stress", "stretch", "strike", "string", "strip", "stroke",
            "strong", "strongly", "structure", "struggle", "student", "studio",
            "study", "stuff", "stupid", "style", "subject", "submit", "subsequent",
            "substance", "substantial", "succeed", "success", "successful", "successfully",
            "such", "sudden", "suddenly", "sue", "suffer", "sufficient", "sugar",
            "suggest", "suggestion", "suicide", "suit", "summer", "summit", "sun",
            "super", "supply", "support", "supporter", "suppose", "supposed", "supreme",
            "sure", "surely", "surface", "surgery", "surprise", "surprised", "surprising",
            "surprisingly", "surround", "survey", "survival", "survive", "survivor",
            "suspect", "sustain", "swear", "sweep", "sweet", "swim", "swing", "switch",
            "symbol", "symptom", "system", "table", "tablespoon", "tactic", "tail",
            "take", "tale", "talent", "talk", "tall", "tank", "tap", "tape", "target",
            "task", "taste", "tax", "taxpayer", "tea", "teach", "teacher", "teaching",
            "team", "tear", "teaspoon", "technical", "technique", "technology", "teen",
            "teenager", "telephone", "telescope", "television", "tell", "temperature",
            "temporary", "ten", "tend", "tendency", "tennis", "tension", "tent",
            "term", "terms", "terrible", "territory", "terror", "terrorism", "terrorist",
            "test", "testify", "testimony", "testing", "text", "than", "thank",
            "thanks", "that", "the", "theater", "their", "them", "theme", "themselves",
            "then", "theory", "therapy", "there", "therefore", "these", "they",
            "thick", "thin", "thing", "think", "thinking", "third", "thirty", "this",
            "those", "though", "thought", "thousand", "threat", "threaten", "three",
            "throat", "through", "throughout", "throw", "thus", "ticket", "tie", "tight",
            "time", "tiny", "tip", "tire", "tired", "tissue", "title", "to", "tobacco",
            "today", "toe", "together", "tomato", "tomorrow", "tone", "tongue", "tonight",
            "too", "tool", "tooth", "top", "topic", "toss", "total", "totally", "touch",
            "tough", "tour", "tourist", "tournament", "toward", "towards", "tower",
            "town", "toy", "trace", "track", "trade", "tradition", "traditional",
            "traffic", "tragedy", "trail", "train", "training", "transfer", "transform",
            "transformation", "transition", "translate", "transportation", "travel",
            "treat", "treatment", "treaty", "tree", "tremendous", "trend", "trial",
            "tribe", "trick", "trip", "troop", "trouble", "truck", "true", "truly",
            "trust", "truth", "try", "tube", "tunnel", "turn", "tv", "twelve",
            "twenty", "twice", "twin", "two", "type", "typical", "typically", "ugly",
            "ultimate", "ultimately", "unable", "uncle", "under", "undergo", "understand",
            "understanding", "unfortunately", "uniform", "union", "unique", "unit",
            "united", "universal", "universe", "university", "unknown", "unless",
            "unlike", "unlikely", "until", "unusual", "up", "upon", "upper", "urban",
            "urge", "us", "use", "used", "useful", "user", "usual", "usually",
            "utility", "vacation", "valley", "valuable", "value", "variable", "variation",
            "variety", "various", "vary", "vast", "vegetable", "vehicle", "venture",
            "version", "versus", "very", "vessel", "veteran", "via", "victim", "victory",
            "video", "view", "viewer", "village", "violate", "violation", "violence",
            "violent", "virtually", "virtue", "virus", "visible", "vision", "visit",
            "visitor", "visual", "vital", "voice", "volume", "volunteer", "vote",
            "voter", "vs", "vulnerable", "wage", "wait", "wake", "walk", "wall",
            "wander", "want", "war", "warm", "warn", "warning", "wash", "waste",
            "watch", "water", "wave", "way", "we", "weak", "wealth", "wealthy",
            "weapon", "wear", "weather", "wedding", "week", "weekend", "weekly",
            "weigh", "weight", "welcome", "welfare", "well", "west", "western", "wet",
            "what", "whatever", "wheel", "when", "whenever", "where", "whereas",
            "whether", "which", "while", "whisper", "white", "who", "whole", "whom",
            "whose", "why", "wide", "widely", "widespread", "wife", "wild", "will",
            "willing", "win", "wind", "window", "wine", "wing", "winner", "winter",
            "wipe", "wire", "wisdom", "wise", "wish", "with", "withdraw", "within",
            "without", "witness", "woman", "wonder", "wonderful", "wood", "wooden",
            "word", "work", "worker", "working", "works", "workshop", "world",
            "worried", "worry", "worth", "would", "wound", "wrap", "write", "writer",
            "writing", "wrong", "yard", "yeah", "year", "yell", "yellow", "yes",
            "yesterday", "yet", "yield", "you", "young", "your", "yours", "yourself",
            "youth", "zone",
            # 6字母单词
            "abroad", "accept", "access", "across", "action", "active", "actual",
            "addition", "adequate", "adjust", "administration", "administrator",
            "admire", "admission", "admit", "adopt", "adult", "advance", "advanced",
            "advantage", "adventure", "advertising", "advice", "advise", "adviser",
            "advocate", "affair", "affect", "afford", "afraid", "african", "after",
            "afternoon", "again", "against", "agency", "agenda", "agent", "agree",
            "agreement", "agricultural", "ahead", "aircraft", "airline", "airport",
            "album", "alcohol", "alive", "alliance", "allow", "almost", "alone",
            "along", "already", "alter", "although", "always", "amazing", "american",
            "among", "amount", "analysis", "analyst", "analyze", "ancient", "anger",
            "angle", "angry", "animal", "anniversary", "announce", "annual", "another",
            "answer", "anticipate", "anxiety", "anybody", "anymore", "anyone",
            "anything", "anyway", "anywhere", "apart", "apartment", "apparent",
            "apparently", "appeal", "appear", "appearance", "apple", "application",
            "apply", "appoint", "appointment", "appreciate", "approach", "appropriate",
            "approval", "approve", "architect", "area", "argue", "argument", "arise",
            "armed", "army", "around", "arrange", "arrangement", "arrest", "arrival",
            "arrive", "article", "artist", "artistic", "asian", "aside", "asleep",
            "aspect", "assault", "assert", "assess", "assessment", "asset", "assign",
            "assignment", "assist", "assistance", "assistant", "associate", "association",
            "assume", "assumption", "assure", "athlete", "athletic", "atmosphere",
            "attach", "attack", "attempt", "attend", "attention", "attitude", "attorney",
            "attract", "attractive", "attribute", "audience", "author", "authority",
            "auto", "available", "average", "avoid", "award", "aware", "awareness",
            "awful", "baby", "back", "background", "badly", "balance", "barely",
            "barrel", "barrier", "baseball", "basic", "basically", "basis", "basket",
            "basketball", "bathroom", "battery", "battle", "beach", "bean", "bear",
            "beat", "beautiful", "beauty", "because", "become", "bedroom", "beer",
            "before", "begin", "beginning", "behavior", "behind", "being", "belief",
            "believe", "bell", "belong", "below", "belt", "bench", "bend", "beneath",
            "benefit", "beside", "besides", "best", "better", "between", "beyond",
            "bible", "bicycle", "bill", "billion", "bind", "biological", "bird",
            "birth", "birthday", "bitter", "black", "blade", "blame", "blanket",
            "blind", "block", "blood", "board", "boat", "body", "bomb", "bombing",
            "bond", "bone", "book", "boom", "boot", "border", "born", "borrow",
            "boss", "both", "bother", "bottle", "bottom", "boundary", "bowl", "box",
            "brain", "branch", "brand", "bread", "break", "breakfast", "breast",
            "breath", "breathe", "brick", "bridge", "brief", "briefly", "bright",
            "brilliant", "bring", "british", "broad", "broken", "brother", "brown",
            "brush", "buck", "budget", "build", "building", "bullet", "bunch",
            "burden", "burn", "bury", "business", "busy", "butter", "button", "buyer",
            "cabin", "cabinet", "cable", "cake", "calculate", "call", "camera",
            "camp", "campaign", "campus", "canadian", "cancer", "candidate", "capable",
            "capacity", "capital", "captain", "capture", "carbon", "card", "care",
            "career", "careful", "carefully", "carrier", "carry", "case", "cash",
            "cast", "casualty", "catch", "category", "catholic", "cause", "ceiling",
            "celebrate", "celebration", "celebrity", "cell", "center", "central",
            "century", "ceremony", "certain", "certainly", "chain", "chair", "chairman",
            "challenge", "chamber", "champion", "championship", "chance", "change",
            "changing", "channel", "chapter", "character", "characteristic", "characterize",
            "charge", "charity", "chart", "chase", "cheap", "check", "cheek", "cheese",
            "chef", "chemical", "chest", "chicken", "chief", "child", "childhood",
            "chinese", "chip", "chocolate", "choice", "cholesterol", "choose",
            "chronic", "chunk", "church", "cigarette", "circle", "circumstance",
            "citizen", "city", "civil", "civilian", "claim", "class", "classic",
            "classroom", "clean", "clear", "clearly", "client", "climate", "climb",
            "clinic", "clinical", "clock", "close", "closely", "clothes", "cloud",
            "club", "clue", "cluster", "coach", "coal", "coast", "coat", "code",
            "coffee", "cognitive", "cold", "collapse", "colleague", "collect",
            "collection", "collective", "college", "colonial", "color", "column",
            "combination", "combine", "come", "comedy", "comfort", "comfortable",
            "command", "commander", "comment", "commercial", "commission", "commit",
            "commitment", "committee", "common", "communicate", "communication",
            "community", "company", "compare", "comparison", "compete", "competition",
            "competitive", "competitor", "complain", "complaint", "complete",
            "completely", "complex", "complicated", "component", "compose",
            "composition", "comprehensive", "computer", "concentrate", "concentration",
            "concept", "concern", "concerned", "concert", "conclude", "conclusion",
            "concrete", "condition", "conduct", "conference", "confidence",
            "confident", "confirm", "conflict", "confront", "confusion", "congress",
            "connect", "connection", "consciousness", "consensus", "consequence",
            "conservative", "consider", "considerable", "consideration", "consist",
            "consistent", "constant", "constantly", "constitute", "constitutional",
            "construct", "construction", "consultant", "consume", "consumer",
            "consumption", "contact", "contain", "container", "contemporary",
            "content", "contest", "context", "continue", "contract", "contrast",
            "contribute", "contribution", "control", "controversial", "controversy",
            "convention", "conventional", "conversation", "convert", "conviction",
            "convince", "cook", "cookie", "cooking", "cool", "cooperation", "cop",
            "cope", "copy", "core", "corn", "corner", "corporate", "corporation",
            "correct", "correspondent", "cost", "cotton", "couch", "could", "council",
            "counselor", "count", "counter", "country", "county", "couple", "courage",
            "course", "court", "cousin", "cover", "coverage", "cow", "crack",
            "craft", "crash", "crazy", "cream", "create", "creation", "creative",
            "creature", "credit", "crew", "crime", "criminal", "crisis", "criteria",
            "critic", "critical", "criticism", "criticize", "crop", "cross", "crowd",
            "crucial", "cry", "cultural", "culture", "cup", "curious", "current",
            "currently", "curriculum", "custom", "customer", "cut", "cycle", "dad",
            "daily", "damage", "dance", "danger", "dangerous", "dark", "darkness",
            "data", "date", "daughter", "dead", "deal", "dealer", "dear", "death",
            "debate", "debt", "decade", "decide", "decision", "deck", "declare",
            "decline", "decrease", "deep", "deeply", "deer", "defeat", "defend",
            "defendant", "defense", "defensive", "deficit", "define", "definitely",
            "definition", "degree", "delay", "deliver", "delivery", "demand",
            "democracy", "democratic", "demonstrate", "demonstration", "deny",
            "department", "depend", "dependent", "depending", "depict", "depression",
            "depth", "deputy", "derive", "describe", "description", "desert",
            "deserve", "design", "designer", "desire", "desk", "desperate",
            "despite", "destroy", "destruction", "detail", "detailed", "detect",
            "determine", "develop", "developing", "development", "device", "devote",
            "dialogue", "die", "diet", "differ", "difference", "different",
            "differently", "difficult", "difficulty", "dig", "digital", "dimension",
            "dining", "dinner", "direct", "direction", "directly", "director",
            "dirt", "dirty", "disability", "disagree", "disappear", "disaster",
            "discipline", "discourse", "discover", "discovery", "discrimination",
            "discuss", "discussion", "disease", "dish", "dismiss", "disorder",
            "display", "dispute", "distance", "distant", "distinct", "distinction",
            "distinguish", "distribute", "distribution", "district", "diverse",
            "diversity", "divide", "division", "divorce", "doctor", "document",
            "domestic", "dominant", "dominate", "door", "double", "doubt", "down",
            "downtown", "dozen", "draft", "drag", "drama", "dramatic", "dramatically",
            "draw", "drawing", "dream", "dress", "drink", "drive", "driver", "drop",
            "drug", "dry", "due", "during", "dust", "duty", "each", "eager", "ear",
            "early", "earn", "earnings", "earth", "ease", "easily", "east", "eastern",
            "easy", "eat", "economic", "economics", "economist", "economy", "edge",
            "edition", "editor", "educate", "education", "educational", "educator",
            "effect", "effective", "effectively", "efficiency", "efficient",
            "effort", "egg", "eight", "either", "elderly", "elect", "election",
            "electric", "electricity", "electronic", "element", "elementary",
            "eliminate", "elite", "else", "elsewhere", "embrace", "emerge",
            "emergency", "emission", "emotion", "emotional", "emphasis", "emphasize",
            "employ", "employee", "employer", "employment", "empty", "enable",
            "encounter", "encourage", "end", "enemy", "energy", "enforcement",
            "engage", "engine", "engineer", "engineering", "english", "enhance",
            "enjoy", "enormous", "enough", "ensure", "enter", "enterprise",
            "entertainment", "entire", "entirely", "entrance", "entry", "environment",
            "environmental", "episode", "equal", "equally", "equipment", "era",
            "error", "escape", "especially", "essay", "essential", "essentially",
            "establish", "establishment", "estate", "estimate", "ethical", "european",
            "evaluate", "evaluation", "even", "evening", "event", "eventually",
            "ever", "every", "everybody", "everyday", "everyone", "everything",
            "everywhere", "evidence", "evolution", "evolve", "exact", "exactly",
            "examination", "examine", "example", "exceed", "excellent", "except",
            "exception", "exchange", "exciting", "executive", "exercise", "exhibit",
            "exhibition", "exist", "existence", "existing", "expand", "expansion",
            "expect", "expectation", "expense", "expensive", "experience",
            "experiment", "expert", "explain", "explanation", "explode", "explore",
            "explosion", "expose", "exposure", "express", "expression", "extend",
            "extension", "extensive", "extent", "external", "extra", "extraordinary",
            "extreme", "extremely", "eye", "fabric", "face", "facility", "fact",
            "factor", "factory", "faculty", "fade", "fail", "failure", "fair",
            "fairly", "faith", "fall", "false", "familiar", "family", "famous",
            "fan", "fancy", "fantasy", "far", "farm", "farmer", "fashion",
            "fast", "fat", "fate", "father", "fault", "favor", "favorite", "fear",
            "feature", "federal", "fee", "feed", "feel", "feeling", "fellow",
            "female", "fence", "festival", "fetch", "few", "fewer", "fiber",
            "fiction", "field", "fifteen", "fifth", "fifty", "fight", "fighter",
            "fighting", "figure", "file", "fill", "film", "final", "finally",
            "finance", "financial", "find", "finding", "fine", "finger", "finish",
            "fire", "firm", "first", "fish", "fishing", "fit", "fitness", "five",
            "fix", "flag", "flame", "flat", "flavor", "flee", "flesh", "flight",
            "float", "floor", "flow", "flower", "fly", "focus", "folk", "follow",
            "following", "food", "foot", "football", "for", "force", "foreign",
            "forest", "forever", "forget", "form", "formal", "formation", "former",
            "formula", "forth", "fortune", "forward", "found", "foundation",
            "founder", "four", "fourth", "frame", "framework", "free", "freedom",
            "freeze", "french", "frequency", "frequent", "frequently", "fresh",
            "friend", "friendly", "friendship", "from", "front", "fruit",
            "frustration", "fuel", "full", "fully", "fun", "function", "fund",
            "fundamental", "funding", "funeral", "funny", "furniture", "further",
            "future", "gain", "galaxy", "gallery", "game", "gang", "gap", "garage",
            "garden", "garlic", "gas", "gate", "gather", "gay", "gaze", "gear",
            "gender", "gene", "general", "generally", "generate", "generation",
            "genetic", "gentleman", "gently", "german", "gesture", "get", "ghost",
            "giant", "gift", "gifted", "girl", "girlfriend", "give", "given",
            "glad", "glance", "glass", "global", "glove", "goal", "god", "gold",
            "golden", "golf", "good", "government", "governor", "grab", "grade",
            "gradually", "graduate", "grain", "grand", "grandfather", "grandmother",
            "grant", "grass", "grave", "gray", "great", "greatest", "green", "grocery",
            "ground", "group", "grow", "growing", "growth", "guarantee", "guard",
            "guess", "guest", "guide", "guideline", "guilty", "gun", "guy", "habit",
            "habitat", "hair", "half", "hall", "hand", "handful", "handle", "hang",
            "happen", "happy", "hard", "hardly", "hat", "hate", "have", "head",
            "headline", "headquarters", "health", "healthy", "hear", "hearing",
            "heart", "heat", "heaven", "heavily", "heavy", "heel", "height", "hell",
            "hello", "help", "helpful", "hence", "her", "here", "heritage", "hero",
            "herself", "hide", "high", "highlight", "highly", "highway", "hill",
            "him", "himself", "hip", "hire", "his", "historian", "historic",
            "historical", "history", "hit", "hold", "hole", "holiday", "holy",
            "home", "homeless", "honest", "honey", "honor", "hope", "horizon",
            "horror", "horse", "hospital", "host", "hot", "hotel", "hour", "house",
            "household", "housing", "how", "however", "huge", "human", "humor",
            "hundred", "hungry", "hunt", "hunter", "hunting", "hurt", "husband",
            "hypothesis",
            # 7-8字母单词
            "ability", "absence", "academy", "account", "achieve", "acquire",
            "address", "advance", "adverse", "advised", "adviser", "against",
            "airline", "airport", "alcohol", "alleged", "already", "analyst",
            "analyze", "announce", "another", "anxiety", "anxious", "anybody",
            "anymore", "anyone", "anything", "anyway", "anywhere", "apparent",
            "appeal", "appear", "appoint", "archive", "arrange", "arrival",
            "article", "assault", "assumed", "assure", "attempt", "attract",
            "auction", "average", "backing", "balance", "banking", "barrier",
            "battery", "bearing", "beating", "because", "bedroom", "believe",
            "belong", "benefit", "besides", "between", "billion", "binding",
            "brother", "brought", "burning", "cabinet", "caliber", "calling",
            "capable", "capital", "captain", "capture", "careful", "carrier",
            "caution", "ceiling", "central", "centric", "century", "certain",
            "chamber", "channel", "chapter", "charity", "charlie", "charter",
            "checked", "chicken", "chronic", "circuit", "civil", "claimed",
            "clarify", "classic", "climate", "closing", "closure", "clothes",
            "collect", "college", "combine", "comfort", "command", "comment",
            "compact", "company", "compare", "compete", "complex", "concept",
            "concern", "concert", "conduct", "confirm", "connect", "consent",
            "consist", "consult", "contact", "contain", "content", "contest",
            "context", "control", "convert", "convince", "cooking", "correct",
            "council", "counsel", "counter", "country", "courage", "course",
            "covered", "created", "creator", "crucial", "crystal", "culture",
            "current", "cutting", "dealing", "decided", "declare", "decline",
            "defense", "defined", "deficit", "deliver", "demand", "denying",
            "depart", "depend", "deposit", "depress", "depth", "deputy",
            "derived", "deserve", "design", "desired", "despite", "destroy",
            "develop", "devoted", "diamond", "digital", "discuss", "disease",
            "display", "dispute", "distant", "diverse", "divided", "drawing",
            "driving", "dynamic", "eastern", "economy", "edition", "elderly",
            "element", "embrace", "emerged", "emotion", "empathy", "empire",
            "employ", "endless", "endorse", "enemy", "energy", "enforce",
            "engage", "engine", "enhance", "enjoyed", "enormous", "ensured",
            "entered", "entire", "entries", "episode", "equally", "essence",
            "ethical", "evening", "evident", "exactly", "examine", "example",
            "excited", "exclude", "excuse", "exhibit", "expansion", "expect",
            "expert", "explain", "explode", "explore", "express", "extract",
            "extreme", "factory", "faculty", "failure", "fashion", "feature",
            "federal", "feeling", "fiction", "fifteen", "fighter", "finally",
            "finance", "finding", "fishing", "fitness", "foreign", "forever",
            "formula", "fortune", "forward", "foundation", "founder", "freedom",
            "further", "gallery", "general", "genetic", "genuine", "gesture",
            "getting", "glasses", "glimpse", "greater", "grocery", "growing",
            "habitat", "halfway", "hallway", "handful", "happily", "headache",
            "healthy", "hearing", "heavily", "helpful", "herself", "highest",
            "highway", "himself", "history", "holiday", "housing", "however",
            "hundred", "husband", "illegal", "illness", "imagine", "improve",
            "include", "initial", "inquiry", "insight", "install", "instant",
            "instead", "intense", "intention",
        ]
    
    def _save_word_list(self):
        """保存单词列表到文件"""
        try:
            word_file = self.data_dir / "words.json"
            with open(word_file, "w", encoding="utf-8") as f:
                json.dump(self.word_list, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存单词列表失败: {e}")
    
    def _load_idiom_data(self):
        """加载成语数据"""
        # 内置成语数据
        builtin_idioms = self._get_builtin_idioms()
        
        # 尝试从文件加载
        idiom_file = self.data_dir / "idioms.json"
        if idiom_file.exists():
            try:
                with open(idiom_file, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                    if isinstance(file_data, dict):
                        self.idiom_data = {**builtin_idioms, **file_data}
                        logger.info(f"从文件加载了 {len(file_data)} 个成语")
                    else:
                        self.idiom_data = builtin_idioms
            except Exception as e:
                logger.warning(f"加载成语文件失败: {e}")
                self.idiom_data = builtin_idioms
        else:
            self.idiom_data = builtin_idioms
            # 保存内置成语到文件
            self._save_idiom_data()
        
        logger.info(f"成语库加载完成，共 {len(self.idiom_data)} 个成语")
    
    def _get_builtin_idioms(self) -> Dict[str, Dict]:
        """获取内置成语数据"""
        return {
            "一心一意": {"pinyin": "yī xīn yī yì", "meaning": "心思、意念专一"},
            "二话不说": {"pinyin": "èr huà bù shuō", "meaning": "不说别的话，立即行动"},
            "三心二意": {"pinyin": "sān xīn èr yì", "meaning": "又想这样又想那样，犹豫不定"},
            "四面八方": {"pinyin": "sì miàn bā fāng", "meaning": "各个方面或各个地方"},
            "五湖四海": {"pinyin": "wǔ hú sì hǎi", "meaning": "指全国各地，有时也指世界各地"},
            "六神无主": {"pinyin": "liù shén wú zhǔ", "meaning": "形容心慌意乱，拿不定主意"},
            "七上八下": {"pinyin": "qī shàng bā xià", "meaning": "形容心里慌乱不安"},
            "八仙过海": {"pinyin": "bā xiān guò hǎi", "meaning": "比喻各自拿出本领或办法，互相竞赛"},
            "九牛一毛": {"pinyin": "jiǔ niú yī máo", "meaning": "比喻极大数量中极微小的数量"},
            "十全十美": {"pinyin": "shí quán shí měi", "meaning": "十分完美，毫无欠缺"},
            "画蛇添足": {"pinyin": "huà shé tiān zú", "meaning": "比喻做了多余的事，非但无益，反而不合适"},
            "守株待兔": {"pinyin": "shǒu zhū dài tù", "meaning": "比喻死守狭隘经验，不知变通"},
            "亡羊补牢": {"pinyin": "wáng yáng bǔ láo", "meaning": "比喻出了问题以后想办法补救"},
            "掩耳盗铃": {"pinyin": "yǎn ěr dào líng", "meaning": "比喻自己欺骗自己"},
            "买椟还珠": {"pinyin": "mǎi dú huán zhū", "meaning": "比喻没有眼力，取舍不当"},
            "自相矛盾": {"pinyin": "zì xiāng máo dùn", "meaning": "比喻自己说话做事前后抵触"},
            "刻舟求剑": {"pinyin": "kè zhōu qiú jiàn", "meaning": "比喻拘泥不知变通"},
            "狐假虎威": {"pinyin": "hú jiǎ hǔ wēi", "meaning": "比喻依仗别人的势力欺压人"},
            "井底之蛙": {"pinyin": "jǐng dǐ zhī wā", "meaning": "比喻见识狭窄的人"},
            "杯弓蛇影": {"pinyin": "bēi gōng shé yǐng", "meaning": "比喻因疑神疑鬼而引起恐惧"},
            "画龙点睛": {"pinyin": "huà lóng diǎn jīng", "meaning": "比喻作文或说话时在关键地方加上精辟的语句"},
            "对牛弹琴": {"pinyin": "duì niú tán qín", "meaning": "比喻对不懂道理的人讲道理"},
            "望梅止渴": {"pinyin": "wàng méi zhǐ kě", "meaning": "比喻愿望无法实现，用空想安慰自己"},
            "卧薪尝胆": {"pinyin": "wò xīn cháng dǎn", "meaning": "形容人刻苦自励，发奋图强"},
            "破釜沉舟": {"pinyin": "pò fǔ chén zhōu", "meaning": "比喻下决心不顾一切地干到底"},
            "草木皆兵": {"pinyin": "cǎo mù jiē bīng", "meaning": "形容人在惊慌时疑神疑鬼"},
            "纸上谈兵": {"pinyin": "zhǐ shàng tán bīng", "meaning": "比喻空谈理论，不能解决实际问题"},
            "指鹿为马": {"pinyin": "zhǐ lù wéi mǎ", "meaning": "比喻故意颠倒黑白，混淆是非"},
            "四面楚歌": {"pinyin": "sì miàn chǔ gē", "meaning": "比喻陷入四面受敌的困境"},
            "背水一战": {"pinyin": "bèi shuǐ yī zhàn", "meaning": "比喻与敌人决一死战"},
            "一鸣惊人": {"pinyin": "yī míng jīng rén", "meaning": "比喻平时没有突出的表现，突然做出惊人的成绩"},
            "一鼓作气": {"pinyin": "yī gǔ zuò qì", "meaning": "比喻趁劲头大的时候鼓起干劲"},
            "半途而废": {"pinyin": "bàn tú ér fèi", "meaning": "比喻做事不能坚持到底"},
            "锲而不舍": {"pinyin": "qiè ér bù shě", "meaning": "比喻有恒心，有毅力"},
            "水滴石穿": {"pinyin": "shuǐ dī shí chuān", "meaning": "比喻只要有恒心，不断努力，事情就一定能成功"},
            "胸有成竹": {"pinyin": "xiōng yǒu chéng zhú", "meaning": "比喻做事之前已经有通盘的考虑"},
            "熟能生巧": {"pinyin": "shú néng shēng qiǎo", "meaning": "比喻熟练了就能产生巧办法"},
            "举一反三": {"pinyin": "jǔ yī fǎn sān", "meaning": "比喻从一件事情类推而知道其他许多事情"},
            "触类旁通": {"pinyin": "chù lèi páng tōng", "meaning": "比喻掌握了某一事物的知识或规律，进而推知同类事物的知识或规律"},
            "融会贯通": {"pinyin": "róng huì guàn tōng", "meaning": "把各方面的知识和道理融化汇合，得到全面透彻的理解"},
            "学以致用": {"pinyin": "xué yǐ zhì yòng", "meaning": "为了实际应用而学习"},
            "温故知新": {"pinyin": "wēn gù zhī xīn", "meaning": "温习旧的知识，得到新的理解和体会"},
            "不耻下问": {"pinyin": "bù chǐ xià wèn", "meaning": "乐于向学问或地位比自己低的人学习，而不觉得不好意思"},
            "孜孜不倦": {"pinyin": "zī zī bù juàn", "meaning": "指工作或学习勤奋不知疲倦"},
            "精益求精": {"pinyin": "jīng yì qiú jīng", "meaning": "好了还求更好"},
            "实事求是": {"pinyin": "shí shì qiú shì", "meaning": "指从实际对象出发，探求事物的内部联系及其发展的规律性"},
            "脚踏实地": {"pinyin": "jiǎo tà shí dì", "meaning": "比喻做事踏实，认真"},
            "持之以恒": {"pinyin": "chí zhī yǐ héng", "meaning": "长久坚持下去"},
            "坚持不懈": {"pinyin": "jiān chí bù xiè", "meaning": "坚持到底，一点不松懈"},
            "全力以赴": {"pinyin": "quán lì yǐ fù", "meaning": "把全部力量都投入进去"},
            "百发百中": {"pinyin": "bǎi fā bǎi zhòng", "meaning": "形容射箭或打枪准确，每次都命中目标"},
            "百步穿杨": {"pinyin": "bǎi bù chuān yáng", "meaning": "形容箭法或枪法十分高明"},
            "百折不挠": {"pinyin": "bǎi zhé bù náo", "meaning": "比喻意志坚强，无论受到多少次挫折，毫不动摇退缩"},
            "百战百胜": {"pinyin": "bǎi zhàn bǎi shèng", "meaning": "每战必胜，形容所向无敌"},
            "百依百顺": {"pinyin": "bǎi yī bǎi shùn", "meaning": "什么都依从，形容一切都顺从别人"},
            "千军万马": {"pinyin": "qiān jūn wàn mǎ", "meaning": "形容雄壮的队伍或浩大的声势"},
            "千变万化": {"pinyin": "qiān biàn wàn huà", "meaning": "形容变化极多"},
            "千差万别": {"pinyin": "qiān chā wàn bié", "meaning": "形容各类多，差别大"},
            "千钧一发": {"pinyin": "qiān jūn yī fà", "meaning": "比喻情况万分危急"},
            "万紫千红": {"pinyin": "wàn zǐ qiān hóng", "meaning": "形容百花齐放，色彩艳丽"},
            "万众一心": {"pinyin": "wàn zhòng yī xīn", "meaning": "千万人一条心，形容团结一致"},
            "万无一失": {"pinyin": "wàn wú yī shī", "meaning": "指非常有把握，绝对不会出差错"},
            "万象更新": {"pinyin": "wàn xiàng gēng xīn", "meaning": "事物或景象改换了样子，出现了一番新气象"},
        }
    
    def _save_idiom_data(self):
        """保存成语数据到文件"""
        try:
            idiom_file = self.data_dir / "idioms.json"
            with open(idiom_file, "w", encoding="utf-8") as f:
                json.dump(self.idiom_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存成语数据失败: {e}")
    
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
        """获取汉字拼音"""
        # 从成语数据中提取拼音
        for idiom, data in self.idiom_data.items():
            if char in idiom and "pinyin" in data:
                pinyin_str = data["pinyin"]
                # 解析拼音字符串
                parts = pinyin_str.split()
                for i, c in enumerate(idiom):
                    if c == char and i < len(parts):
                        return parts[i]
        
        # 简化的拼音映射（作为后备）
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
            "地": "di4", "持": "chi2", "恒": "heng2", "坚": "jian1", "持": "chi2",
            "懈": "xie4", "全": "quan2", "力": "li4", "赴": "fu4", "说": "shuo1",
            "话": "hua4", "方": "fang1", "便": "bian4", "百": "bai3", "发": "fa1",
            "中": "zhong4", "步": "bu4", "杨": "yang2", "折": "zhe2", "挠": "nao2",
            "胜": "sheng4", "依": "yi1", "顺": "shun4", "千": "qian1", "军": "jun1",
            "万": "wan4", "马": "ma3", "变": "bian4", "化": "hua4", "差": "cha1",
            "别": "bie2", "钧": "jun1", "发": "fa4", "紫": "zi3", "红": "hong2",
            "众": "zhong4", "象": "xiang4", "更": "geng1", "无": "wu2", "失": "shi1",
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
        msg = f"""🎮 WordMaster - 词汇大师 游戏帮助 v1.2.0

📚 题库信息:
• 单词库: {len(self.word_list)} 个单词 (5-8字母)
• 成语库: {len(self.idiom_data)} 个成语

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
