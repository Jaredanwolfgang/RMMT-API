from dataclasses import dataclass

from matching_v2 import MatchV2Config, calculate_match_v2, calculate_numeric_score


@dataclass
class Item:
    id: str
    title: str
    type: str
    params: dict
    data_type: str = "string"


@dataclass
class Answer:
    item_id: str
    answer: str
    weight: float
    item: Item
    vector: str | None = None


@dataclass
class User:
    questionnaire_answers: list[Answer]


class FakeEmbeddingModel:
    def encode(self, text):
        text = text.lower()
        if "音乐" in text or "music" in text:
            return [1.0, 0.0, 0.0]
        if "游戏" in text or "game" in text:
            return [0.2, 0.98, 0.0]
        if "安静" in text or "quiet" in text:
            return [0.9, 0.1, 0.0]
        if "热闹" in text or "party" in text:
            return [0.1, 0.9, 0.0]
        return [0.5, 0.5, 0.0]


OPTIONS_5 = ["1", "2", "3", "4", "5"]

sleep_item = Item("sleep", "你通常几点睡觉？", "radio", {"options": OPTIONS_5})
clean_item = Item("clean", "你平时对宿舍整洁程度的习惯是？", "radio", {"options": OPTIONS_5})
hobby_item = Item("hobby", "你的兴趣爱好是什么？", "text", {})
intro_item = Item("intro", "请简单介绍一下你自己", "text", {})


def answer(item, value, weight=1):
    return Answer(item.id, str(value), weight, item)


def user(*answers):
    return User(list(answers))


def test_same_answers_score_higher_than_different_answers():
    a = user(answer(sleep_item, 2), answer(clean_item, 4))
    same = user(answer(sleep_item, 2), answer(clean_item, 4))
    different = user(answer(sleep_item, 5), answer(clean_item, 1))

    assert calculate_numeric_score(a, same)["numeric_score"] > calculate_numeric_score(a, different)["numeric_score"]


def test_large_difference_gets_lower_score():
    a = user(answer(sleep_item, 1))
    near = user(answer(sleep_item, 2))
    far = user(answer(sleep_item, 5))

    assert calculate_numeric_score(a, near)["numeric_score"] == 0.75
    assert calculate_numeric_score(a, far)["numeric_score"] == 0.0


def test_higher_weight_has_more_impact():
    a = user(answer(sleep_item, 1, weight=10), answer(clean_item, 1, weight=1))
    differs_on_high_weight = user(answer(sleep_item, 5, weight=10), answer(clean_item, 1, weight=1))
    differs_on_low_weight = user(answer(sleep_item, 1, weight=10), answer(clean_item, 5, weight=1))

    high_weight_score = calculate_numeric_score(a, differs_on_high_weight)["numeric_score"]
    low_weight_score = calculate_numeric_score(a, differs_on_low_weight)["numeric_score"]
    assert high_weight_score < low_weight_score


def test_text_similarity_contributes_to_final_score():
    a = user(answer(sleep_item, 3), answer(hobby_item, "喜欢音乐和安静的宿舍"))
    text_similar = user(answer(sleep_item, 3), answer(hobby_item, "music and quiet"))
    text_different = user(answer(sleep_item, 3), answer(hobby_item, "game party"))

    cfg = MatchV2Config(alpha=0.5, beta=0.5)
    similar_score = calculate_match_v2(a, text_similar, config=cfg, embedding_model=FakeEmbeddingModel())
    different_score = calculate_match_v2(a, text_different, config=cfg, embedding_model=FakeEmbeddingModel())

    assert similar_score["text_score"] > different_score["text_score"]
    assert similar_score["match_score"] > different_score["match_score"]


def test_text_cosine_uses_plus_one_half_mapping():
    a = user(answer(hobby_item, "music"))
    weak = user(answer(hobby_item, "game"))

    score = calculate_match_v2(a, weak, config=MatchV2Config(alpha=0.0, beta=1.0), embedding_model=FakeEmbeddingModel())

    assert 0.5 < score["text_score"] < 1.0


def test_missing_answers_are_neutral_and_do_not_crash():
    a = user(answer(sleep_item, 3), answer(intro_item, "喜欢安静"))
    empty = user()

    result = calculate_match_v2(a, empty, embedding_model=FakeEmbeddingModel())
    assert result["numeric_score"] == 0.5
    assert result["text_score"] == 0.5
    assert result["match_score"] == 50.0


def run_all():
    tests = [
        test_same_answers_score_higher_than_different_answers,
        test_large_difference_gets_lower_score,
        test_higher_weight_has_more_impact,
        test_text_similarity_contributes_to_final_score,
        test_text_cosine_uses_plus_one_half_mapping,
        test_missing_answers_are_neutral_and_do_not_crash,
    ]
    for test in tests:
        test()
    print(f"matching_v2 tests passed: {len(tests)}")


if __name__ == "__main__":
    run_all()
