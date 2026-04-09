"""Skill 3 侵权检测单元测试。

测试 copyright_checker 的风险评估逻辑和 checker 的合并逻辑。
"""

import pytest
from unittest.mock import patch, MagicMock

from models.compliance import ComplianceResult, ComplianceIssue, ComplianceLevel
from skills.compliance_checker.copyright_checker import (
    CopyrightRisk,
    _assess_risk,
    check_copyright_batch,
    STOCK_DOMAINS,
    IP_LABELS,
)
from skills.compliance_checker.checker import _merge_copyright


# ── _assess_risk 单元测试 ──────────────────────────────────


class TestAssessRisk:
    """测试 Vision API 响应 → CopyrightRisk 映射。"""

    def test_empty_response_is_low(self):
        r = _assess_risk({})
        assert r.risk == "low"
        assert not r.logos
        assert not r.stock_hits
        assert not r.ip_hits

    def test_logo_detected_is_high(self):
        r = _assess_risk({
            "logoAnnotations": [
                {"description": "Nike", "score": 0.95},
                {"description": "Adidas", "score": 0.80},
            ]
        })
        assert r.risk == "high"
        assert "Nike" in r.logos
        assert "Adidas" in r.logos
        assert any("品牌Logo" in reason for reason in r.reasons)

    def test_stock_domain_is_high(self):
        for domain in ["shutterstock.com", "gettyimages.com", "vcg.com"]:
            r = _assess_risk({
                "webDetection": {
                    "fullMatchingImages": [{"url": f"https://www.{domain}/photo/123"}],
                    "partialMatchingImages": [],
                    "pagesWithMatchingImages": [],
                }
            })
            assert r.risk == "high", f"{domain} should be high risk"
            assert domain in r.stock_hits

    def test_multiple_full_matches_is_medium(self):
        """3+ 完全匹配但非素材库 → medium。"""
        r = _assess_risk({
            "webDetection": {
                "fullMatchingImages": [
                    {"url": f"https://random-site-{i}.com/img.jpg"} for i in range(5)
                ],
                "partialMatchingImages": [],
                "pagesWithMatchingImages": [],
            }
        })
        assert r.risk == "medium"
        assert any("完全匹配" in reason for reason in r.reasons)

    def test_many_domains_is_medium(self):
        """被 5+ 个网站使用 → medium。"""
        r = _assess_risk({
            "webDetection": {
                "fullMatchingImages": [],
                "partialMatchingImages": [
                    {"url": f"https://site-{i}.com/page"} for i in range(6)
                ],
                "pagesWithMatchingImages": [],
            }
        })
        assert r.risk == "medium"
        assert any("网站使用" in reason for reason in r.reasons)

    def test_ip_label_is_medium(self):
        """IP 标签（cartoon/anime 等）→ medium。"""
        r = _assess_risk({
            "labelAnnotations": [
                {"description": "Cartoon", "score": 0.90},
                {"description": "Fictional character", "score": 0.85},
            ]
        })
        assert r.risk == "medium"
        assert len(r.ip_hits) >= 1
        assert any("IP形象" in reason for reason in r.reasons)

    def test_ip_label_not_triggered_by_normal_labels(self):
        """普通标签不触发 IP 检测。"""
        r = _assess_risk({
            "labelAnnotations": [
                {"description": "Kitchen", "score": 0.95},
                {"description": "Cooking", "score": 0.90},
                {"description": "Stainless steel", "score": 0.85},
            ]
        })
        assert r.risk == "low"
        assert not r.ip_hits

    def test_logo_overrides_medium(self):
        """Logo (high) 不会被 IP 标签降到 medium。"""
        r = _assess_risk({
            "logoAnnotations": [{"description": "Disney", "score": 0.99}],
            "labelAnnotations": [{"description": "Cartoon", "score": 0.90}],
        })
        assert r.risk == "high"

    def test_stock_plus_ip_stays_high(self):
        """素材库 + IP 标签 → high（不降级）。"""
        r = _assess_risk({
            "webDetection": {
                "fullMatchingImages": [{"url": "https://shutterstock.com/img"}],
                "partialMatchingImages": [],
                "pagesWithMatchingImages": [],
            },
            "labelAnnotations": [{"description": "Anime", "score": 0.80}],
        })
        assert r.risk == "high"
        assert r.stock_hits
        assert r.ip_hits


# ── _merge_copyright 单元测试 ──────────────────────────────


class TestMergeCopyright:
    """测试合规结果与侵权检测结果的合并逻辑。"""

    def _make_result(self, shot_id, level=ComplianceLevel.PASS, score=1.0):
        return ComplianceResult(
            shot_id=shot_id, frame_path=f"shot_{shot_id:02d}.png",
            level=level, score=score, summary="OK",
        )

    def test_high_upgrades_pass_to_fail(self):
        results = [self._make_result(1)]
        copyright_risks = {
            1: CopyrightRisk(risk="high", reasons=["品牌Logo: Nike"], logos=["Nike"])
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        assert results[0].level == ComplianceLevel.FAIL
        assert results[0].score == 0.2
        assert any(i.category == "copyright_logo" for i in results[0].issues)
        assert "no Nike logo" in results[0].error_keywords
        assert 1 in error_kw

    def test_medium_upgrades_pass_to_warn(self):
        results = [self._make_result(1)]
        copyright_risks = {
            1: CopyrightRisk(risk="medium", reasons=["疑似IP形象: Cartoon"], ip_hits=["Cartoon"])
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        assert results[0].level == ComplianceLevel.WARN
        assert results[0].score == 0.6
        assert any(i.category == "copyright_ip" for i in results[0].issues)
        assert "疑似侵权" in results[0].summary

    def test_low_does_not_change(self):
        results = [self._make_result(1)]
        copyright_risks = {
            1: CopyrightRisk(risk="low", reasons=["未发现侵权"])
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        assert results[0].level == ComplianceLevel.PASS
        assert results[0].score == 1.0
        assert results[0].summary == "OK"

    def test_unknown_does_not_change(self):
        results = [self._make_result(1)]
        copyright_risks = {
            1: CopyrightRisk(risk="unknown", reasons=["API 错误"])
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        assert results[0].level == ComplianceLevel.PASS
        assert results[0].score == 1.0

    def test_high_does_not_downgrade_existing_fail(self):
        """已经 FAIL 的不会被 high 改变 level（保持 FAIL）。"""
        results = [self._make_result(1, ComplianceLevel.FAIL, 0.2)]
        copyright_risks = {
            1: CopyrightRisk(risk="high", reasons=["品牌Logo: Gucci"], logos=["Gucci"])
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        assert results[0].level == ComplianceLevel.FAIL
        assert results[0].score == 0.2
        assert any("copyright_logo" in i.category for i in results[0].issues)

    def test_medium_does_not_downgrade_warn(self):
        """WARN + medium → WARN（不降级）。"""
        results = [self._make_result(1, ComplianceLevel.WARN, 0.6)]
        copyright_risks = {
            1: CopyrightRisk(risk="medium", reasons=["3 个完全匹配"])
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        assert results[0].level == ComplianceLevel.WARN
        assert results[0].score == 0.6

    def test_stock_generates_correct_keywords(self):
        results = [self._make_result(1)]
        copyright_risks = {
            1: CopyrightRisk(
                risk="high",
                reasons=["素材库来源: shutterstock.com"],
                stock_hits=["shutterstock.com"],
            )
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        kw = results[0].error_keywords
        assert "original photo" in kw
        assert "no stock image" in kw

    def test_multiple_shots_independent(self):
        """多个 shot 独立合并，互不影响。"""
        results = [
            self._make_result(1),
            self._make_result(2),
            self._make_result(3),
        ]
        copyright_risks = {
            1: CopyrightRisk(risk="high", reasons=["Logo"], logos=["Nike"]),
            2: CopyrightRisk(risk="low", reasons=["安全"]),
            # shot 3 不在 copyright_risks 中
        }
        error_kw = {}
        _merge_copyright(results, copyright_risks, error_kw)

        assert results[0].level == ComplianceLevel.FAIL  # high → FAIL
        assert results[1].level == ComplianceLevel.PASS   # low → 不变
        assert results[2].level == ComplianceLevel.PASS   # 无数据 → 不变

    def test_empty_copyright_risks(self):
        """空侵权结果不影响任何 shot。"""
        results = [self._make_result(1)]
        _merge_copyright(results, {}, {})
        assert results[0].level == ComplianceLevel.PASS


# ── check_copyright_batch 集成测试（mock API）──────────────


class TestCheckCopyrightBatch:

    @patch("config.settings")
    def test_no_api_key_returns_empty(self, mock_settings):
        mock_settings.GOOGLE_VISION_API_KEY = ""
        result = check_copyright_batch({1: "shot_01.png"})
        assert result == {}

    def test_empty_frame_paths_returns_empty(self):
        result = check_copyright_batch({})
        assert result == {}

    @patch("skills.compliance_checker.copyright_checker._call_vision_batch")
    @patch("skills.compliance_checker.copyright_checker._compress_for_vision")
    @patch("config.settings")
    def test_batch_call_and_parse(self, mock_settings, mock_compress, mock_batch):
        mock_settings.GOOGLE_VISION_API_KEY = "fake-key"
        mock_compress.return_value = "base64data"
        mock_batch.return_value = {
            "responses": [
                {"logoAnnotations": [{"description": "Nike", "score": 0.95}]},
                {},  # 无侵权
            ]
        }

        result = check_copyright_batch({1: "shot_01.png", 2: "shot_02.png"})

        assert len(result) == 2
        assert result[1].risk == "high"
        assert result[2].risk == "low"

    @patch("skills.compliance_checker.copyright_checker._call_vision_batch")
    @patch("skills.compliance_checker.copyright_checker._compress_for_vision")
    @patch("config.settings")
    def test_api_error_returns_unknown(self, mock_settings, mock_compress, mock_batch):
        mock_settings.GOOGLE_VISION_API_KEY = "fake-key"
        mock_compress.return_value = "base64data"
        mock_batch.return_value = {"error": "HTTP 403: Forbidden"}

        result = check_copyright_batch({1: "shot_01.png"})

        assert result[1].risk == "unknown"
        assert any("API错误" in r for r in result[1].reasons)
