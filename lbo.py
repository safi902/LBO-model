"""
Simple LBO (leveraged buyout) model.

Steps:
  1. Sources & Uses   - how the deal is paid for
  2. Projections      - revenue, EBITDA, free cash flow for each year
  3. Debt schedule    - interest, mandatory repayment, cash sweep
  4. Returns          - exit value, equity value, IRR and MOIC
  5. Returns bridge   - where the profit came from

Run:  python lbo.py            (uses inputs.yaml)
"""

import yaml
import pandas as pd


def load_inputs(path="inputs.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def irr(cash_flows, low=-0.99, high=10.0, tol=1e-7):
    """Internal rate of return by bisection (no extra libraries needed)."""
    def npv(rate):
        return sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))
    for _ in range(200):
        mid = (low + high) / 2
        if npv(mid) > 0:
            low = mid
        else:
            high = mid
        if high - low < tol:
            break
    return (low + high) / 2


def run_lbo(inp):
    e, fin, op, ex = inp["entry"], inp["financing"], inp["operations"], inp["exit"]
    years = op["years"]

    # ---------- 1. Sources & Uses ----------
    purchase_price = e["ltm_ebitda"] * e["entry_multiple"]
    fees = purchase_price * e["transaction_fees_pct"]
    uses_total = purchase_price + fees + e["minimum_cash"]

    senior_debt = e["ltm_ebitda"] * fin["senior_debt_multiple"]
    sub_debt = e["ltm_ebitda"] * fin["sub_debt_multiple"]
    sponsor_equity = uses_total - senior_debt - sub_debt   # PE firm's check

    sources_uses = pd.DataFrame({
        "Sources": ["Senior debt", "Subordinated notes", "Sponsor equity", "Total"],
        "Sources ($m)": [senior_debt, sub_debt, sponsor_equity, uses_total],
        "Uses": ["Purchase price", "Transaction fees", "Cash to balance sheet", "Total"],
        "Uses ($m)": [purchase_price, fees, e["minimum_cash"], uses_total],
    })

    # ---------- 2 & 3. Projections and debt schedule ----------
    rows = []
    revenue = op["starting_revenue"]
    prev_nwc = revenue * op["nwc_pct_revenue"]
    senior, sub = senior_debt, sub_debt
    mandatory = senior_debt * fin["senior_mandatory_amort_pct"]

    for y in range(1, years + 1):
        revenue *= 1 + op["revenue_growth"][y - 1]
        ebitda = revenue * op["ebitda_margin"][y - 1]
        da = revenue * op["da_pct_revenue"]
        capex = revenue * op["capex_pct_revenue"]
        nwc = revenue * op["nwc_pct_revenue"]
        change_nwc = nwc - prev_nwc
        prev_nwc = nwc

        # Interest on the opening balance (simple and avoids circular math)
        interest = senior * fin["senior_interest_rate"] + sub * fin["sub_interest_rate"]
        ebt = ebitda - da - interest
        taxes = max(ebt, 0) * op["tax_rate"]
        net_income = ebt - taxes

        # Free cash flow available to repay debt
        fcf = net_income + da - capex - change_nwc

        # Repay senior debt: mandatory first, then sweep leftover cash
        mand_paid = min(mandatory, senior)
        senior -= mand_paid
        sweep = max(fcf - mand_paid, 0) * fin["cash_sweep_pct"]
        sweep = min(sweep, senior)
        senior -= sweep

        rows.append({
            "Year": y, "Revenue": revenue, "EBITDA": ebitda, "EBITDA margin": ebitda / revenue,
            "D&A": da, "Interest": interest, "Taxes": taxes, "Net income": net_income,
            "Capex": capex, "Change in NWC": change_nwc, "Free cash flow": fcf,
            "Mandatory repayment": mand_paid, "Cash sweep": sweep,
            "Senior debt (end)": senior, "Sub notes (end)": sub,
            "Total debt (end)": senior + sub,
            "Leverage (Debt/EBITDA)": (senior + sub) / ebitda,
        })

    proj = pd.DataFrame(rows).set_index("Year")

    # ---------- 4. Returns ----------
    exit_ebitda = proj["EBITDA"].iloc[-1]
    exit_ev = exit_ebitda * ex["exit_multiple"]
    exit_debt = proj["Total debt (end)"].iloc[-1]
    exit_equity = exit_ev - exit_debt + e["minimum_cash"]

    moic = exit_equity / sponsor_equity
    cash_flows = [-sponsor_equity] + [0] * (years - 1) + [exit_equity]
    deal_irr = irr(cash_flows)

    # ---------- 5. Returns bridge: where did the profit come from? ----------
    entry_debt = senior_debt + sub_debt
    ebitda_growth = (exit_ebitda - e["ltm_ebitda"]) * e["entry_multiple"]
    multiple_change = (ex["exit_multiple"] - e["entry_multiple"]) * exit_ebitda
    debt_paydown = entry_debt - exit_debt
    fees_drag = -fees
    bridge = pd.Series({
        "EBITDA growth": ebitda_growth,
        "Multiple expansion": multiple_change,
        "Debt paydown": debt_paydown,
        "Transaction fees": fees_drag,
    })
    # Check: equity gain should equal the sum of the bridge
    gain = exit_equity - sponsor_equity
    assert abs(bridge.sum() - gain) < 1e-6, (bridge.sum(), gain)

    returns = {
        "Sponsor equity invested ($m)": sponsor_equity,
        "Exit EBITDA ($m)": exit_ebitda,
        "Exit enterprise value ($m)": exit_ev,
        "Debt at exit ($m)": exit_debt,
        "Equity value at exit ($m)": exit_equity,
        "MOIC (x)": moic,
        "IRR": deal_irr,
    }
    return sources_uses, proj, returns, bridge


def sensitivity(inp, exit_multiples=(8, 9, 10, 11, 12), leverage=(4.5, 5.0, 5.5, 6.0, 6.5)):
    """IRR for different exit multiples (columns) and total entry leverage (rows)."""
    base_sub = inp["financing"]["sub_debt_multiple"]
    table = {}
    for lev in leverage:
        row = {}
        for m in exit_multiples:
            x = yaml.safe_load(yaml.safe_dump(inp))  # deep copy
            x["exit"]["exit_multiple"] = m
            x["financing"]["senior_debt_multiple"] = lev - base_sub
            row[f"{m:.1f}x exit"] = run_lbo(x)[2]["IRR"]
        table[f"{lev:.1f}x leverage"] = row
    return pd.DataFrame(table).T


def main():
    inp = load_inputs()
    su, proj, ret, bridge = run_lbo(inp)
    sens = sensitivity(inp)

    pd.set_option("display.width", 160)
    print(f"\n=== {inp['company']} ===\n")
    print("Sources & Uses ($m)\n", su.round(1).to_string(index=False), "\n")
    print("Projections ($m)\n", proj.T.round(1).to_string(), "\n")
    print("Returns")
    for k, v in ret.items():
        print(f"  {k:32s} {v:.1%}" if k == "IRR" else f"  {k:32s} {v:,.2f}")
    print("\nWhere the equity gain came from ($m)\n", bridge.round(1).to_string())
    print("\nIRR sensitivity (rows = entry leverage, columns = exit multiple)\n",
          (sens * 100).round(1).astype(str).add("%").to_string())

    # Excel export
    with pd.ExcelWriter("output/lbo_model.xlsx") as xl:
        pd.DataFrame(list(ret.items()), columns=["Metric", "Value"]).to_excel(xl, "Returns", index=False)
        su.to_excel(xl, "Sources & Uses", index=False)
        proj.T.to_excel(xl, "Projections")
        bridge.rename("$m").to_frame().to_excel(xl, "Returns bridge")
        sens.to_excel(xl, "IRR sensitivity")

    # Charts
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].bar(proj.index, proj["Total debt (end)"], color="#4C72B0", label="Total debt")
    ax2 = ax[0].twinx()
    ax2.plot(proj.index, proj["EBITDA"], color="#DD8452", marker="o", label="EBITDA")
    ax[0].set_title("Debt falls while EBITDA grows")
    ax[0].set_xlabel("Year"); ax[0].set_ylabel("Total debt ($m)"); ax2.set_ylabel("EBITDA ($m)")

    colors = ["#55A868" if v >= 0 else "#C44E52" for v in bridge]
    ax[1].barh(bridge.index, bridge.values, color=colors)
    ax[1].set_title(f"Sources of equity gain  (MOIC {ret['MOIC (x)']:.2f}x, IRR {ret['IRR']:.1%})")
    ax[1].set_xlabel("$m")
    fig.tight_layout()
    fig.savefig("output/lbo_summary.png", dpi=150)
    print("\nSaved output/lbo_model.xlsx and output/lbo_summary.png")


if __name__ == "__main__":
    main()
