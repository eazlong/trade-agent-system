"""`POST api/regime/truth/tidy/`：AI 整理 / 预检端点的**契约面**。

判定与转写的逻辑在 `test_truth_tidy` 里钉；这个文件只钉「端点这一层」的六条不变量：

1. **认证必需**：匿名 401。它是读面（不写库），所以普通登录用户就够——不钉 staff。
2. **一行都不写库**：请求前后 `RegimeTruthInterval` 的行数不变。这是这个端点最要紧的
   一条：它一旦写了，AI 的转写就成了比对的基准，而整条「人工标注」的正当性就没了。
3. **失败是 200 不是 5xx**：整理失败时回 `status="failed"` + `error`，前端能原样显示给
   人看。用 5xx 会让它被渲染成「后端挂了」，而人该做的动作是改写材料。
4. **输入错是 400**：空原文 / 首尾空白 / 超长 / 没有 raw 字段——那是调用方的错。
5. **与命令行同一条链**：响应里的 `csv` 必须能被 `check_truth_csv.parse_text` 直接读，
   且 `problems` 与它算出来的一致。两条路分家的话，同一个材料在两处会得出两个结论，
   而两边各自看都自洽。
6. **不回显算法结论、不缓存**：响应里没有任何量化标签/一致率，且 `Cache-Control: no-store`
   （同一段原文每次转写都可能不同，缓存它等于把随机产物伪装成稳定事实）。
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.regime import truth_tidy
from apps.regime.management.commands import check_truth_csv
from apps.regime.models import RegimeTruthInterval

URL = "/api/regime/truth/tidy/"

#: 一份正常的转写结果。**档位用原文的词**（不归一），source_text 原样抄。
TIDIED = {
    "intervals": [
        {
            "start": "2020-03-12",
            "end": "2020-03-13",
            "regime": "高波动",
            "note": "312",
            "source_text": "3 月 12 日到 13 日：高波动（312 暴跌）",
        },
        {
            "start": "2020-03-14",
            "end": "2020-03-14",
            "regime": "",
            "note": "看不出",
            "source_text": "3 月 14 日：看不出",
        },
    ]
}


def _outcome(payload: dict) -> truth_tidy.TidyOutcome:
    """走**生产代码**的校验器与渲染，不手搓 `TidyOutcome`。

    手搓的话，「端点用的是不是真的那条链」这件事就没有被验证——而它正是本文件要钉的
    那一条（第 5 条）。
    """
    spans = truth_tidy._validate(payload)
    return truth_tidy.TidyOutcome(
        status=truth_tidy.STATUS_OK,
        csv=truth_tidy.render_csv(spans),
        intervals=spans,
    )


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            email="tidy@example.com", password="pw", username="tidy"
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class TestItWritesNothing(_Base):
    """第 2 条：**一行都不写库**。这一组红了的后果比其它几组都重。"""

    def test_a_successful_tidy_creates_no_truth_interval(self):
        before = RegimeTruthInterval.objects.count()
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(TIDIED)):
            response = self.client.post(URL, {"raw": "原文"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            RegimeTruthInterval.objects.count(),
            before,
            "整理端点写库了——AI 的转写从此成了比对的基准",
        )

    def test_a_failed_tidy_creates_no_truth_interval(self):
        before = RegimeTruthInterval.objects.count()
        failed = truth_tidy.TidyOutcome(
            status=truth_tidy.STATUS_FAILED,
            csv="",
            error="LLM 没有给出可用答复：timeout",
        )
        with patch.object(truth_tidy, "tidy_raw", return_value=failed):
            response = self.client.post(URL, {"raw": "原文"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(RegimeTruthInterval.objects.count(), before)


class TestAuth(_Base):
    """第 1 条：读面权限（登录即可），且 401 不是 403。"""

    def test_anonymous_is_401(self):
        anonymous = APIClient()
        response = anonymous.post(URL, {"raw": "原文"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_a_plain_user_is_let_through(self):
        """不写库的端点按 ADR 0005 属于读面：普通登录用户就够，不要求 staff。"""
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(TIDIED)):
            response = self.client.post(URL, {"raw": "原文"}, format="json")
        self.assertEqual(response.status_code, 200)


class TestTheHappyPath(_Base):
    def test_it_returns_the_csv_the_rows_and_the_merged_intervals(self):
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(TIDIED)):
            response = self.client.post(URL, {"raw": "原文"}, format="json")

        data = response.json()
        self.assertEqual(data["status"], "ok")
        # rows 是 AI 的原始产物：两条区间（其中一条档位为空）。
        self.assertEqual(len(data["rows"]), 2)
        self.assertIn("起始日期,结束日期,档位,备注,原文", data["csv"])

        # 03-12..03-13 这条直接成为一段 2 天；03-14 档位为空 ⇒ 不成为区间。
        self.assertEqual(len(data["intervals"]), 1)
        interval = data["intervals"][0]
        self.assertEqual(interval["start"], "2020-03-12")
        self.assertEqual(interval["end"], "2020-03-13")
        self.assertEqual(interval["days"], 2)
        self.assertEqual(interval["regime"]["value"], "high_vol")
        self.assertEqual(interval["regime"]["display"], "高波动")

    def test_a_blank_regime_row_contributes_no_interval(self):
        """档位空 = 没标，它既不是错误（`problems` 里没有它），也不是一段区间。"""
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(TIDIED)):
            data = self.client.post(URL, {"raw": "原文"}, format="json").json()

        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(
            len(data["intervals"]), 1, "两天的区间是一段，空档位那条不成为区间"
        )
        self.assertEqual(data["problems"], [], "空档位不该报任何问题")


class TestTheSameChainAsTheCommand(_Base):
    """第 5 条：端点与 `check_truth_csv --ai-tidy` 共用 `parse_text`。"""

    def test_the_returned_csv_is_parseable_by_the_command(self):
        outcome = _outcome(TIDIED)
        with patch.object(truth_tidy, "tidy_raw", return_value=outcome):
            data = self.client.post(URL, {"raw": "原文"}, format="json").json()

        rows, problems = check_truth_csv.parse_text(data["csv"])
        self.assertEqual(problems, [])
        # 解析器**只产出填了档位的那条**：空档位是「没标」，不是一条待录入的记录。
        # 两条区间转写进来、一条解析出去——这正是「不填空白」的落点。
        self.assertEqual(len(rows), 1, "空档位那条被解析器跳过")
        self.assertEqual(rows[0].span[1].isoformat(), "2020-03-13", "终点没有被丢掉")

    def test_an_unrecognised_word_shows_up_as_a_format_problem(self):
        """`极端波动` 走不到 intervals，而是落成一条「格式」问题。

        这正是「词表只有一处」的落点：端点自己不判档位，它把判断让给 `parse_text`。
        """
        payload = {
            "intervals": [
                {
                    "start": "2020-03-12",
                    "end": "2020-03-12",
                    "regime": "极端波动",
                    "note": "",
                    "source_text": "3/12 极端波动",
                }
            ]
        }
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(payload)):
            data = self.client.post(URL, {"raw": "原文"}, format="json").json()

        self.assertEqual(data["intervals"], [])
        self.assertEqual(len(data["problems"]), 1)
        self.assertEqual(data["problems"][0]["kind"], "格式")
        self.assertTrue(data["problems"][0]["blocking"])
        self.assertIn("极端波动", data["problems"][0]["detail"])

    def test_a_self_conflict_is_reported_by_the_endpoint(self):
        """同一天两个不同档位：端点要报出来，且**不替人合并**。

        **用两个非叠加档位**（下行 + 箱体）：高波动是叠加层，与另一档并存是
        「这段在跌、其中某天崩了」，按 `truth.fold_overlay` 不算冲突。
        """
        payload = {
            "intervals": [
                {
                    "start": "2020-03-12",
                    "end": "2020-03-20",
                    "regime": "下行趋势",
                    "note": "",
                    "source_text": "3/12 到 3/20 下行趋势",
                },
                {
                    "start": "2020-03-15",
                    "end": "2020-03-25",
                    "regime": "箱体震荡",
                    "note": "",
                    "source_text": "3/15 到 3/25 箱体震荡",
                },
            ]
        }
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(payload)):
            data = self.client.post(URL, {"raw": "原文"}, format="json").json()

        kinds = [p["kind"] for p in data["problems"]]
        self.assertIn("自相矛盾", kinds)


class TestFailuresAreNot5xx(_Base):
    """第 3 条：整理失败 → 200 + `status="failed"`。"""

    def test_a_failed_tidy_is_200_with_a_reason(self):
        failed = truth_tidy.TidyOutcome(
            status=truth_tidy.STATUS_FAILED,
            csv="",
            error="LLM 的答复不合形状：没有 rows",
        )
        with patch.object(truth_tidy, "tidy_raw", return_value=failed):
            response = self.client.post(URL, {"raw": "原文"}, format="json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "failed")
        self.assertIn("没有 rows", data["error"])
        self.assertEqual(data["csv"], "")
        self.assertEqual(data["rows"], [])

    def test_a_dead_chain_reaches_the_client_as_a_reason(self):
        """真的走一遍失败路径（不打桩 `tidy_raw`），确认那句原因透得出去。"""
        from apps.agent.llm_client import LLMResponseError

        class Dead:
            async def chat_json(
                self, *, system, user, validate, max_tokens=None, timeout=None
            ):
                raise LLMResponseError("两条降级链都失败：timeout")

        with patch.object(truth_tidy.LLMClient, "get_instance", return_value=Dead()):
            response = self.client.post(URL, {"raw": "3/12 高波动"}, format="json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "failed")
        self.assertIn("没有给出可用答复", data["error"])


class TestInputErrorsAre400(_Base):
    """第 4 条：输入错是 400，与「整理失败」分开。"""

    def test_a_missing_raw_field_is_400(self):
        response = self.client.post(URL, {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("raw", response.json()["error"])

    def test_a_non_string_raw_is_400(self):
        response = self.client.post(URL, {"raw": 123}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_an_empty_raw_is_400_not_a_failed_tidy(self):
        """空原文是**调用方的错**：它永远整理不出东西，重试没有意义。"""
        response = self.client.post(URL, {"raw": "   "}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("空", response.json()["error"])

    def test_an_oversized_raw_is_400_and_never_reaches_the_llm(self):
        class NeverCalled:
            async def chat_json(
                self, *, system, user, validate, max_tokens=None, timeout=None
            ):
                raise AssertionError("超长原文不该走到 LLM")

        with patch.object(
            truth_tidy.LLMClient, "get_instance", return_value=NeverCalled()
        ):
            response = self.client.post(
                URL, {"raw": "x" * (truth_tidy.MAX_RAW_CHARS + 1)}, format="json"
            )
        self.assertEqual(response.status_code, 400)


class TestItLeaksNothing(_Base):
    """第 6 条：不回显算法结论、不缓存。"""

    def test_the_payload_has_no_algorithm_side_numbers(self):
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(TIDIED)):
            body = self.client.post(
                URL, {"raw": "原文"}, format="json"
            ).content.decode()

        for forbidden in ("atr", "agreement", "一致率", "label_series", "counted"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body.lower())

    def test_the_response_is_not_cached(self):
        """同一段原文每次都可能转写出不同结果，缓存它等于把随机产物伪装成稳定事实。"""
        with patch.object(truth_tidy, "tidy_raw", return_value=_outcome(TIDIED)):
            response = self.client.post(URL, {"raw": "原文"}, format="json")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_failure_is_not_cached_either(self):
        failed = truth_tidy.TidyOutcome(
            status=truth_tidy.STATUS_FAILED, csv="", error="x"
        )
        with patch.object(truth_tidy, "tidy_raw", return_value=failed):
            response = self.client.post(URL, {"raw": "原文"}, format="json")
        self.assertEqual(response["Cache-Control"], "no-store")
