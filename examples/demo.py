"""Demo đủ 10 tool, không cần LLM, API key hoặc mạng sau khi cài dependency."""

import json

from bank_tools import AuthContext, BankTools, get_tool_definitions


def main():
    bank = BankTools()
    # Chỉ backend tạo context sau khi module login xác nhận phiên hợp lệ.
    context = AuthContext(customer_id="cus_002", logged_in=True)

    # Backend cung cấp ngày thực hoặc ngày giả lập, không lấy ngày do model tự chọn.
    as_of = "2026-10-31"

    def invoke(name, **arguments):
        arguments["as_of"] = as_of
        result = bank.call(name, arguments, context=context)
        if result["error"]:
            raise RuntimeError(result["error"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    customer = invoke("crm_lookup")

    # Các số hồ sơ dưới đây giả lập thông tin khách đã nói/xác nhận trong hội thoại.
    # crm_lookup cố ý không trả thu nhập hoặc dư nợ cho model.
    suitability = invoke("check_suitability", product="home_loan", income=100_000_000,
                         debt=10_000_000, amount=2_000_000_000, term=240, property_value=3_000_000_000)
    if not suitability["data"]["ok"]:
        raise RuntimeError("Hồ sơ demo không phù hợp; cần dừng tư vấn/chuyển người")

    rates = invoke("get_rate", product="home_loan", amount=2_000_000_000, term=240, segment=customer["data"]["segment"])
    promotions = invoke("get_active_promotions", product="home_loan", channel="zalo")
    standard_rate = next(fact for fact in rates["facts"] if fact["key"] == "standard_rate")
    promo_rate = next(fact for fact in promotions["facts"] if fact["key"] == "promo_rate")
    promo_term = next(fact for fact in promotions["facts"] if fact["key"] == "promo_months" and fact["label"] == promo_rate["label"])
    installment = invoke("calc_installment", amount=2_000_000_000, term=240,
                         rate=promo_rate["value"], rate_fact_id=promo_rate["fact_id"],
                         promo_months=promo_term["value"], after_rate=standard_rate["value"],
                         after_rate_fact_id=standard_rate["fact_id"])
    invoke("get_disclosures", product="home_loan", topics=["rate", "promo", "installment"])
    # Demo chủ động lấy gói thẻ; agent thật chỉ gọi nếu khách có nhu cầu.
    invoke("get_card_offer", income=100_000_000, segment=customer["data"]["segment"])
    invoke("send_summary", channel="zalo",
           fact_ids=[fact["fact_id"] for fact in promotions["facts"] + installment["facts"]])
    invoke("get_insurance_offer")
    invoke("schedule_callback", time="2026-11-02T14:00:00+07:00")
    print(f"\nĐã chạy {len(bank.get_tool_calls())}/{len(get_tool_definitions(context=context))} tool; chỉ gửi tin và hẹn lịch GIẢ LẬP.")


if __name__ == "__main__":
    main()
