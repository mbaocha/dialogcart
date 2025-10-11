class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_word = False
        self.word = None

class Trie:
    def __init__(self):
        self.root = TrieNode()
    
    def insert(self, word: str):
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_word = True
        node.word = word

    def search_in_sentence(self, sentence: str):
        sentence = sentence.lower()
        found_words = set()
        n = len(sentence)

        for i in range(n):
            node = self.root
            j = i
            longest_match = None
            while j < n and sentence[j] in node.children:
                node = node.children[sentence[j]]
                if node.is_word:
                    longest_match = node.word
                j += 1
            if longest_match:
                found_words.add(longest_match)
        return list(found_words)


def longest_matching_words(sentence: str, wordList: list[str]):
    trie = Trie()
    for word in wordList:
        trie.insert(word.lower())
    return trie.search_in_sentence(sentence)


def main():
    sentence = "can you grab one pair of nike running shoes?"
    wordList = ["nike", "house", "cat", "shoes"]

    result = longest_matching_words(sentence, wordList)
    print("Sentence:", sentence)
    print("Word list:", wordList)
    print("Matched words:", result)


if __name__ == "__main__":
    main()
