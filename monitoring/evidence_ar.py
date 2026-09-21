"""Arabic wording for the ranked evidence findings.

This is the file that decides whether the Arabic support in this product is real
or decorative.

The tempting shortcut is to generate the briefing in English and translate it —
either with a model at send time (slow, costly, and capable of altering a
number) or with a phrase table over finished prose (which produces the stilted
register that tells a reader immediately they are using a foreign product).

Neither is what happens here.  Each finding has a stable ``key``
(:class:`~agents.insights.evidence.EvidenceItem`), and each key has an Arabic
sentence written *as Arabic business writing*, carrying the identical
``{{citation}}`` tokens as its English counterpart.  The figure registry
substitutes the same computed values into either one.  So the Arabic briefing is
not a translation of the English briefing — the two are siblings rendered from
the same arithmetic, and neither can drift from the numbers or from each other.

A key with no Arabic template falls back to English rather than to a machine
translation: a sentence in the wrong language is obvious and harmless, while a
confidently mistranslated financial claim is neither.
"""

from __future__ import annotations

# Keys match agents.insights.evidence's `add(..., key=...)` arguments.
#
# STRENGTH and CAVEAT findings are never delivered as alerts (see
# monitoring/rules.py), so only the tags that can interrupt someone are
# translated here — everything else would be effort spent on text no one in the
# Arabic path ever receives.
AR_EVIDENCE: dict[str, str] = {
    # ── What changed ───────────────────────────────────────────────────────
    "period_move": (
        "تغيّر الإيراد بمقدار {{mon_revenue_change}} ({{mon_revenue_change_pct}}) "
        "مقارنةً بالفترة السابقة، من {{mon_prior_revenue}} إلى {{mon_revenue}}."
    ),
    "period_move_p30": (
        "تغيّر الإيراد بمقدار {{p30_revenue_change}} ({{p30_revenue_change_pct}}) "
        "مقارنةً بالفترة السابقة، من {{p30_prior_revenue}} إلى {{p30_revenue}}."
    ),
    "fallers_concentrated": (
        "الانخفاض مُركَّز في منتجات بعينها ولا يتوزّع على التشكيلة كلها: "
        "{{faller1_name}} بانخفاض {{faller1_loss}} ({{faller1_change_pct}}). "
        "تسمية المنتج هي ما يحوّل «المبيعات تنخفض» إلى إجراء يمكن اتخاذه هذا الأسبوع."
    ),
    "fallers_offset": (
        "بقي الإيراد الإجمالي ثابتًا، لكن هذا الثبات يخفي حركة تحته: تراجع "
        "{{faller1_name}} بمقدار {{faller1_loss}} وعوّضه غيره. يستحق المتابعة لا "
        "رد الفعل — لكن إذا توقّف التعويض بدأ الإجمالي في الانخفاض."
    ),

    # ── Concentration ──────────────────────────────────────────────────────
    "product_concentration": (
        "{{top_product_name}} وحده يمثّل {{top_product_share}} من إجمالي الإيراد "
        "({{top_product_revenue}})، و{{products_for_80pct}} من أصل {{products_total}} "
        "منتج تصنع {{pareto_threshold}} منه. أي خلل في التوريد أو ارتفاع في السعر أو "
        "منافس على هذا المنتج يضرب النشاط كله دفعةً واحدة."
    ),
    "product_pareto": (
        "{{products_for_80pct}} من أصل {{products_total}} منتج تُنتج "
        "{{pareto_threshold}} من الإيراد. وما دون ذلك يستهلك الجهد ورأس المال العامل "
        "مقابل {{bottom_half_products_share}} فقط من الإيراد."
    ),
    "customer_concentration": (
        "أعلى {{top_decile_pct}} من العملاء ({{top_decile_customers}} حسابًا) يجلبون "
        "{{top_decile_customer_share}} من الإيراد، بينما يجلب النصف الأدنى "
        "{{bottom_half_customer_share}}. فقدان حفنة من هذه الحسابات حدثٌ مؤثّر ماليًا — "
        "وهي تستحق مسؤولًا بالاسم لا قائمة بريدية."
    ),

    # ── Leaks and decisions ────────────────────────────────────────────────
    "margin_drag": (
        "{{margin_drag_revenue}} من الإيراد ({{margin_drag_share}}) موجود في منتجات "
        "هامشها أقل من هامش المحفظة — يتصدّرها {{lowmargin1_name}} عند "
        "{{lowmargin1_margin_pct}} مقابل {{gross_margin_pct}} إجمالًا. رفع هذه المنتجات "
        "وحدها إلى معدّل المحفظة يساوي {{scn_margin_normalisation_upside}} ربحًا إجماليًا "
        "دون أي مبيعات إضافية."
    ),
    "loss_making_product": (
        "{{lossmaker1_name}} يُباع بخسارة إجمالية ({{lossmaker1_loss}}). كل وحدة تُباع "
        "تزيد الوضع سوءًا — القرار هنا سعرٌ أو تكلفةٌ أو إيقاف، لا حملة تسويقية."
    ),
    "discount_leak": (
        "{{discount_given}} مُنِحت خصومات — أي {{discount_share_of_gross}} من القيمة قبل "
        "الخصم، مطبَّقة على {{discount_lines_pct}} من السطور بمتوسط {{discount_avg_pct}}. "
        "الخصم على نصف الدفتر ليس عرضًا ترويجيًا، بل هو قائمة الأسعار الحقيقية."
    ),
    "weak_repeat": (
        "{{repeat_rate_pct}} فقط من العملاء اشتروا مرتين؛ و{{one_time_customers}} عميلًا "
        "اشتروا مرة واحدة ولم يعودوا. دفع كل منهم إلى طلب إضافي بحجم الوسيط يساوي "
        "{{scn_second_purchase_upside}} — وهو أرخص من شراء الإيراد نفسه من عملاء جدد."
    ),
    "weekday_rhythm": (
        "يبلغ متوسط {{best_weekday}} مقدار {{best_weekday_avg}} مقابل {{avg_daily_revenue}} "
        "في يوم عادي ({{best_weekday_uplift_pct}} أفضل)، بينما ينخفض {{worst_weekday}} بنسبة "
        "{{worst_weekday_gap_pct}}. رفع أضعف يوم إلى مستوى اليوم العادي يساوي "
        "{{scn_weak_weekday_uplift}} على مدى الفترة — وهذا قرار جدولة عمالة ومخزون وعروض."
    ),
}

# Short headline per key, for the alert title and the notification preview. The
# body above is what a reader gets when they open it; this is what fits on a
# lock screen.
AR_EVIDENCE_TITLE: dict[str, str] = {
    "period_move": "تغيّر الإيراد بمقدار {{mon_revenue_change}} ({{mon_revenue_change_pct}})",
    "period_move_p30": "تغيّر الإيراد بمقدار {{p30_revenue_change}} ({{p30_revenue_change_pct}})",
    "fallers_concentrated": "الانخفاض مُركَّز في {{faller1_name}} ({{faller1_loss}})",
    "fallers_offset": "تراجع {{faller1_name}} بمقدار {{faller1_loss}} وعوّضه غيره",
    "product_concentration": "{{top_product_name}} يمثّل {{top_product_share}} من الإيراد",
    "product_pareto": "{{products_for_80pct}} منتجات تصنع {{pareto_threshold}} من الإيراد",
    "customer_concentration": (
        "أعلى {{top_decile_pct}} من العملاء يجلبون {{top_decile_customer_share}} من الإيراد"
    ),
    "margin_drag": "{{margin_drag_revenue}} من الإيراد في منتجات دون هامش المحفظة",
    "loss_making_product": "{{lossmaker1_name}} يُباع بخسارة ({{lossmaker1_loss}})",
    "discount_leak": "{{discount_given}} مُنِحت خصومات ({{discount_share_of_gross}} من الإجمالي)",
    "weak_repeat": "{{repeat_rate_pct}} فقط من العملاء اشتروا مرتين",
    "weekday_rhythm": "{{worst_weekday}} أضعف بنسبة {{worst_weekday_gap_pct}} من اليوم العادي",
}


def arabic_for(key: str, *, title: bool = False) -> str | None:
    """The Arabic template for a finding key, or None to fall back to English.

    ``period_move`` is registered under two token prefixes because the evidence
    engine picks whichever comparison window the data supports (calendar month
    first, rolling 30 days second) and the tokens differ between them. Returning
    a template whose tokens are not in the registry would render as
    "[figure unavailable]", so the caller checks before using it.
    """
    table = AR_EVIDENCE_TITLE if title else AR_EVIDENCE
    return table.get(key)
