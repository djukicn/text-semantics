import numpy as np
from flair.data import Sentence
from flair.embeddings import WordEmbeddings, DocumentPoolEmbeddings
from sklearn.feature_extraction.text import TfidfVectorizer
import pickle
import os

from .word_enrichment import hypergeom_p_values


def prepare_data(tokens_list):
    embedder = WordEmbeddings('sl')
    words = list()
    word_embs = list()
    doc_embs = list()
    word2doc = dict()
    doc2word = list()

    for i, tokens in enumerate(tokens_list):
        sent = Sentence(" ".join(tokens))
        embedder.embed(sent)
        doc_emb = np.zeros(embedder.embedding_length)
        doc2word.append(set())
        for token in sent.tokens:
            if token.text not in word2doc:
                word2doc[token.text] = set()
            word2doc[token.text].add(i)
            doc2word[i].add(token.text)

            emb = token.embedding.cpu().detach().numpy()
            doc_emb += emb / len(tokens)
            if token.text not in words:
                words.append(token.text)
                word_embs.append(emb)
        doc_embs.append(doc_emb)

    doc_embs = np.array(doc_embs)
    word_embs = np.array(word_embs)

    return doc_embs, words, word_embs, word2doc, doc2word


def cos_sim(x, y):
    if x.sum() == 0 or y.sum() == 0:
        return 0
    return x.dot(y) / np.linalg.norm(x) / np.linalg.norm(y)


def find_corpus_words(doc_embs, words, word_embs):
    # compute distances
    distances = np.zeros((word_embs.shape[0], doc_embs.shape[0]))
    for i in range(word_embs.shape[0]):
        for j in range(doc_embs.shape[0]):
            distances[i, j] = 1 - cos_sim(word_embs[i, :], doc_embs[j, :])

    # compute scores
    doc_desc = []
    for j in range(doc_embs.shape[0]):
        scores = np.zeros(word_embs.shape[0])
        for i in range(word_embs.shape[0]):
            mask = np.full(doc_embs.shape[0], fill_value=True)
            mask[j] = False
            scores[i] = distances[i, j] - np.mean(distances[i, mask])

        idx = np.argsort(scores)
        doc_desc.append([(words[w], scores[w]) for w in idx])

    return doc_desc


def find_document_words(doc_embs, words, word_embs, word2doc, doc2word):
    word2ind = dict(zip(words, range(len(words))))
    distances = dict()
    for i in range(word_embs.shape[0]):
        word = words[i]
        for j in word2doc[word]:
            distances[i, j] = 1 - cos_sim(word_embs[i, :], doc_embs[j, :])

    doc_desc = list()
    for j in range(doc_embs.shape[0]):
        scores = np.zeros(len(doc2word[j]))
        ind2word = dict(zip(list(range(len(doc2word[j]))), list(doc2word[j])))
        for k, word in enumerate(doc2word[j]):
            # current document and a single word
            i = word2ind[word]
            sum_of_distances = sum([distances[i, x] for x in word2doc[word]])
            if len(word2doc[word]) > 1:
                mean_distance = (sum_of_distances - distances[i, j]) / (len(word2doc[word]) - 1)
            else:
                mean_distance = 0
            scores[k] = distances[i, j] - mean_distance
        idx = np.argsort(scores)
        doc_desc.append([(ind2word[x], scores[x]) for x in idx])
    return doc_desc


def find_cluster_words(doc_embs, words, word_embs, cluster_labels):
    # find unique cluster labels
    unique_clusters = [c for c in np.unique(cluster_labels) if c > -1]
    n_clusters = len(unique_clusters)

    # find centroids
    cluster_centroids = np.zeros((n_clusters, doc_embs.shape[1]))
    for c, c_label in enumerate(unique_clusters):
        cluster_centroids[c, :] = np.mean(doc_embs[cluster_labels == c_label], axis=0)

    # compute distances between centroids and words
    word_distance_vectors = np.zeros((word_embs.shape[0], n_clusters))
    for i in range(word_embs.shape[0]):
        for j, c_label in enumerate(unique_clusters):
            word_distance_vectors[i, j] = 1 - cos_sim(word_embs[i, :],
                                                      cluster_centroids[j, :])

    # describe clusters
    cluster_describer = dict()
    for c, c_label in enumerate(unique_clusters):
        cluster_describer[c_label] = list()
        cluster_metric = np.zeros(word_embs.shape[0])
        for i in range(word_embs.shape[0]):
            mask = np.full(n_clusters, fill_value=True)
            mask[c] = False
            cluster_metric[i] = word_distance_vectors[i, c] - np.mean(
                word_distance_vectors[i, mask])
        inds = np.argsort(cluster_metric)
        for ind in inds:
            cluster_describer[c_label].append(words[ind])

    return cluster_describer


def find_specific_with_pos(tokens, pos_document_embedding=False):
    # POS tags
    path = os.path.join(os.path.dirname(__file__), 'sl-tagger.pickle')
    tagger = pickle.load(open(path, 'rb'))
    tags = tagger.tag(tokens)
    filtered_tags = [t[0] for t in tags if t[1][0] in ['S', 'P']]
    candidates = np.unique([t[0] for t in tags if t[1][0] == 'S'])

    # embedding
    doc_embedder = DocumentPoolEmbeddings([WordEmbeddings('sl')], pooling='mean')
    if pos_document_embedding:
        doc_sent = Sentence(" ".join(filtered_tags))
    else:
        doc_sent = Sentence(" ".join(tokens))
    doc_embedder.embed(doc_sent)
    doc_emb = doc_sent.embedding.cpu().detach().numpy()
    embs = list()
    for candidate in candidates:
        sent = Sentence(candidate)
        doc_embedder.embed(sent)
        embs.append(sent.embedding.cpu().detach().numpy())

    # ranking
    distances = [1 - cos_sim(doc_emb, emb) for emb in embs]
    ranked = [(candidates[i], 1 - distances[i]) for i in np.argsort(distances)]

    return ranked


def embedding_corpus_words(tokens_list):
    doc_embs, words, word_embs, _, _ = prepare_data(tokens_list)
    return find_corpus_words(doc_embs, words, word_embs)


def embedding_document_words(tokens_list):
    doc_embs, words, word_embs, word2doc, doc2word = prepare_data(tokens_list)
    return find_document_words(doc_embs, words, word_embs, word2doc,
                               doc2word)


def embedding_pos_words(tokens_list, pos_document_embedding=False):
    return [find_specific_with_pos(tokens, pos_document_embedding=pos_document_embedding)
            for tokens in tokens_list]


def enrichment_words(tokens_list):
    joined_texts = [' '.join(tokens) for tokens in tokens_list]
    vectorizer = TfidfVectorizer()
    X = vectorizer.fit_transform(joined_texts)
    words = vectorizer.get_feature_names()

    def specific_words_enrichment(document_index):
        p_values = hypergeom_p_values(X, X[document_index])
        order = np.argsort(p_values)
        return [(words[i], p_values[i]) for i in order]

    return [specific_words_enrichment(i) for i in range(len(tokens_list))]


def tfidf_words(tokens_list):
    joined_texts = [' '.join(tokens) for tokens in tokens_list]
    vectorizer = TfidfVectorizer()
    X = vectorizer.fit_transform(joined_texts)
    words = vectorizer.get_feature_names()

    def find_tfidf_words(document_id):
        feature_index = X[document_id, :].nonzero()[1]
        features = [(words[i], X[document_id, i]) for i in feature_index]
        return sorted(features, key=lambda tup: tup[1], reverse=True)

    return [find_tfidf_words(i) for i in range(len(tokens_list))]
