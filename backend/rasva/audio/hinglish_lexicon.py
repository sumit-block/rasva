"""Common Roman Hinglish words and their standard Devanagari spelling.

Used for two things:
- detecting Roman Hinglish (vs English) in Latin-script text, and
- confident Roman-to-Devanagari suggestions (PRD §10.2 step 5).

Keep entries unambiguous and in standard modern spelling. Words that are also
everyday English words are excluded from *detection* (see AMBIGUOUS) but kept
for transliteration inside sentences already judged to be Hinglish.
"""

from __future__ import annotations

WORDS: dict[str, str] = {
    # function words and pronouns
    "hai": "है", "hain": "हैं", "nahi": "नहीं", "nahin": "नहीं", "kya": "क्या",
    "kyun": "क्यों", "kyon": "क्यों", "kaise": "कैसे", "kab": "कब", "kahan": "कहाँ",
    "mein": "में", "main": "मैं", "ka": "का", "ki": "की", "ke": "के", "ko": "को",
    "se": "से", "par": "पर", "pe": "पे", "aur": "और", "bhi": "भी", "toh": "तो",
    "to": "तो", "tha": "था", "thi": "थी", "the": "थे", "ho": "हो", "hoga": "होगा",
    "hogi": "होगी", "raha": "रहा", "rahi": "रही", "rahe": "रहे", "hum": "हम",
    "tum": "तुम", "aap": "आप", "mera": "मेरा", "meri": "मेरी", "mere": "मेरे",
    "tera": "तेरा", "teri": "तेरी", "uska": "उसका", "uski": "उसकी", "unka": "उनका",
    "yeh": "यह", "ye": "ये", "woh": "वह", "wo": "वो", "ek": "एक", "bahut": "बहुत",
    "haan": "हाँ", "ji": "जी", "abhi": "अभी", "phir": "फिर", "fir": "फिर",
    "kuch": "कुछ", "sab": "सब", "jab": "जब", "tab": "तब", "agar": "अगर",
    "lekin": "लेकिन", "kyunki": "क्योंकि", "isliye": "इसलिए", "liye": "लिए",
    "wala": "वाला", "wali": "वाली", "wale": "वाले", "saath": "साथ", "sirf": "सिर्फ़",
    "bilkul": "बिल्कुल", "zaroor": "ज़रूर", "shayad": "शायद", "pehle": "पहले",
    "baad": "बाद", "wahan": "वहाँ", "yahan": "यहाँ", "andar": "अंदर", "bahar": "बाहर",
    # verbs
    "kar": "कर", "karo": "करो", "karna": "करना", "kiya": "किया", "gaya": "गया",
    "gayi": "गई", "gaye": "गए", "chalo": "चलो", "dekho": "देखो", "bolo": "बोलो",
    "bola": "बोला", "kaha": "कहा", "suno": "सुनो", "kehna": "कहना", "samjha": "समझा",
    "samajh": "समझ", "jaana": "जाना", "jana": "जाना", "aana": "आना", "aaya": "आया",
    "aayi": "आई", "diya": "दिया", "liya": "लिया", "mila": "मिला", "mili": "मिली",
    "chahiye": "चाहिए", "sakta": "सकता", "sakti": "सकती", "sakte": "सकते",
    "poocha": "पूछा", "puchha": "पूछा", "wapas": "वापस",
    # common nouns and adjectives
    "accha": "अच्छा", "acha": "अच्छा", "theek": "ठीक", "thik": "ठीक", "aaj": "आज",
    "kal": "कल", "din": "दिन", "raat": "रात", "ghar": "घर", "log": "लोग",
    "bhai": "भाई", "dost": "दोस्त", "pyaar": "प्यार", "pyar": "प्यार", "dil": "दिल",
    "zindagi": "ज़िंदगी", "baat": "बात", "waqt": "वक़्त", "paani": "पानी", "pani": "पानी",
    "khana": "खाना", "kaam": "काम", "naam": "नाम", "maa": "माँ", "beta": "बेटा",
    "beti": "बेटी", "dadi": "दादी", "sach": "सच", "jhooth": "झूठ", "saal": "साल",
    "subah": "सुबह", "shaam": "शाम", "khush": "ख़ुश", "dukh": "दुख", "sundar": "सुंदर",
    "bada": "बड़ा", "badi": "बड़ी", "chhota": "छोटा", "chhoti": "छोटी", "naya": "नया",
    "nayi": "नई", "purana": "पुराना", "raja": "राजा", "rani": "रानी", "guru": "गुरु",
    "yuddh": "युद्ध", "dharma": "धर्म", "dharm": "धर्म", "karma": "कर्म", "karm": "कर्म",
    "shanti": "शांति",
    # more high-frequency words
    "ne": "ने", "hoon": "हूँ", "hun": "हूँ", "karun": "करूँ", "karunga": "करूँगा",
    "karungi": "करूँगी", "hamara": "हमारा", "hamari": "हमारी", "hamare": "हमारे",
    "tumhara": "तुम्हारा", "tumhari": "तुम्हारी", "apna": "अपना", "apni": "अपनी",
    "apne": "अपने", "koi": "कोई", "kaun": "कौन", "kitna": "कितना", "kitni": "कितनी",
    "kis": "किस", "jo": "जो", "jaise": "जैसे", "waise": "वैसे", "ab": "अब", "ya": "या",
    "hua": "हुआ", "hui": "हुई", "hue": "हुए", "lo": "लो", "jao": "जाओ", "aao": "आओ",
    "dekha": "देखा", "socha": "सोचा", "suna": "सुना", "laga": "लगा", "lagta": "लगता",
    "chal": "चल", "bolna": "बोलना", "pata": "पता", "matlab": "मतलब", "sawal": "सवाल",
    "jawab": "जवाब", "duniya": "दुनिया", "desh": "देश", "aankh": "आँख", "aankhon": "आँखों",
    "haath": "हाथ", "muskuraaya": "मुस्कुराया", "muskuraye": "मुस्कुराए", "uthaya": "उठाया",
}  # fmt: skip

# PRD §10.3 priority names and nearby epic vocabulary, with common Roman spellings.
NAMES: dict[str, str] = {
    "krishna": "कृष्ण", "krishn": "कृष्ण", "arjun": "अर्जुन", "arjuna": "अर्जुन",
    "karna": "कर्ण", "karn": "कर्ण", "bhishma": "भीष्म", "bhishm": "भीष्म",
    "yudhishthir": "युधिष्ठिर", "yudhishthira": "युधिष्ठिर", "duryodhan": "दुर्योधन",
    "duryodhana": "दुर्योधन", "dronacharya": "द्रोणाचार्य", "drona": "द्रोण",
    "dhritarashtra": "धृतराष्ट्र", "ashwatthama": "अश्वत्थामा", "kunti": "कुंती",
    "mahabharat": "महाभारत", "mahabharata": "महाभारत", "gita": "गीता", "geeta": "गीता",
    "pandav": "पांडव", "pandava": "पांडव", "kaurav": "कौरव", "kaurava": "कौरव",
    "kurukshetra": "कुरुक्षेत्र", "draupadi": "द्रौपदी", "bhima": "भीम", "bheem": "भीम",
}  # fmt: skip

# Also common English words: never counted as evidence of Hinglish.
AMBIGUOUS = {
    "to", "the", "main", "log", "din", "par", "pe", "ho", "ye", "kar", "sab", "tab",
    "jab", "dil", "bola", "maa", "ki", "ke", "ka", "ek", "hum", "tum", "bhai", "fir",
    "naam", "beta", "raja", "guru", "karma", "dost", "acha",
}  # fmt: skip

ENGLISH_STOPWORDS = {
    "the", "is", "are", "was", "were", "a", "an", "and", "of", "to", "in", "on", "at",
    "for", "with", "this", "that", "it", "he", "she", "they", "we", "you", "i", "be",
    "have", "has", "had", "not", "but", "or", "from", "by", "as", "his", "her", "their",
    "our", "my", "your", "will", "would", "can", "could", "should", "there", "what",
    "when", "where", "who", "which", "how", "why", "all", "so", "if", "then", "than",
    "into", "about", "after", "before", "over", "under", "do", "does", "did", "been",
    "its", "me", "him", "them", "us", "no", "yes", "very", "just", "only", "also",
}  # fmt: skip
