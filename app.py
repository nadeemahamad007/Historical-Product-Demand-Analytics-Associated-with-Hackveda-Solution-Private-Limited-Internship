from pathlib import Path
import pandas as pd
import numpy as np
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Demand & Inventory Planner", page_icon="📦", layout="wide")

st.title("📦 Historical Product Demand | Inventory Planning Studio")
st.caption("Explore historical demand and translate it into transparent, assumption-based replenishment recommendations.")

DEFAULT_FILE = Path(__file__).parent / "Historical Product Demand.csv"

@st.cache_data(show_spinner="Loading and preparing demand data…")
def load_data(uploaded_file=None):
    if uploaded_file is not None:
        raw = pd.read_csv(uploaded_file)
    elif DEFAULT_FILE.exists():
        raw = pd.read_csv(DEFAULT_FILE)
    else:
        return None
    required = {"Product_Code", "Warehouse", "Product_Category", "Date", "Order_Demand"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    df = raw.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
    # Parentheses can occur in some published versions of this dataset.
    demand = df["Order_Demand"].astype(str).str.replace(r"[(),]", "", regex=True).str.strip()
    df["Order_Demand"] = pd.to_numeric(demand, errors="coerce")
    df = df.dropna(subset=["Date", "Order_Demand", "Product_Code", "Warehouse", "Product_Category"])
    df = df[df["Order_Demand"] >= 0]
    df["Order_Demand"] = df["Order_Demand"].astype(float)
    df["Month"] = df["Date"].dt.to_period("M").dt.to_timestamp()
    return df

uploaded = st.sidebar.file_uploader("Upload the demand CSV (optional)", type=["csv"])
try:
    df = load_data(uploaded)
except Exception as exc:
    st.error(f"Could not read the dataset: {exc}")
    st.stop()
if df is None:
    st.warning("Place `Historical Product Demand.csv` beside `app.py`, or upload it using the sidebar.")
    st.stop()

with st.sidebar:
    st.header("Filters")
    warehouses = sorted(df["Warehouse"].unique().tolist())
    categories = sorted(df["Product_Category"].unique().tolist())
    selected_wh = st.multiselect("Warehouse", warehouses, default=warehouses)
    selected_cat = st.multiselect("Product category", categories, default=categories)
    min_date, max_date = df["Date"].min().date(), df["Date"].max().date()
    date_range = st.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    st.divider()
    st.header("Planning assumptions")
    lead_days = st.slider("Supplier lead time (days)", 1, 90, 14)
    review_days = st.slider("Review period (days)", 1, 60, 7)
    service = st.selectbox("Target service level", [0.90, 0.95, 0.975, 0.99], index=1, format_func=lambda x: f"{x:.1%}")
    current_stock = st.number_input("Current stock for selected product (units)", min_value=0, value=0, step=100)
    st.caption("Lead time, service level and current stock are user-supplied assumptions, not fields in the source data.")

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
else:
    start_date, end_date = pd.Timestamp(min_date), pd.Timestamp(max_date)
f = df[df["Warehouse"].isin(selected_wh) & df["Product_Category"].isin(selected_cat) & df["Date"].between(start_date, end_date)].copy()
if f.empty:
    st.info("No rows match the current filters. Select at least one warehouse, category, and date range.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Demand records", f"{len(f):,}")
c2.metric("Total demand", f"{f['Order_Demand'].sum():,.0f}")
c3.metric("Products", f"{f['Product_Code'].nunique():,}")
c4.metric("Warehouses", f"{f['Warehouse'].nunique():,}")

st.subheader("Demand over time")
monthly = f.groupby("Month", as_index=False)["Order_Demand"].sum()
fig = px.line(monthly, x="Month", y="Order_Demand", markers=True, labels={"Month":"Month", "Order_Demand":"Units demanded"})
fig.update_layout(height=360, margin=dict(l=10,r=10,t=20,b=10))
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns(2)
with left:
    st.subheader("Demand by warehouse")
    wh = f.groupby("Warehouse", as_index=False)["Order_Demand"].sum().sort_values("Order_Demand", ascending=False)
    st.plotly_chart(px.bar(wh, x="Warehouse", y="Order_Demand", labels={"Order_Demand":"Units demanded"}), use_container_width=True)
with right:
    st.subheader("Top 15 products by demand")
    top = f.groupby("Product_Code", as_index=False)["Order_Demand"].sum().nlargest(15, "Order_Demand").sort_values("Order_Demand")
    st.plotly_chart(px.bar(top, x="Order_Demand", y="Product_Code", orientation="h", labels={"Order_Demand":"Units demanded"}), use_container_width=True)

st.subheader("Replenishment planning")
st.write("Select a product and warehouse to estimate a reorder point and target stock from daily historical demand. This is a planning heuristic, not a purchase order or a cost-optimized LP.")
choices = f[["Product_Code", "Warehouse", "Product_Category"]].drop_duplicates().sort_values(["Product_Code", "Warehouse"])
choices["Choice"] = choices["Product_Code"] + " | " + choices["Warehouse"] + " | " + choices["Product_Category"]
choice = st.selectbox("Product / warehouse", choices["Choice"].tolist())
selected = choices.loc[choices["Choice"] == choice].iloc[0]
series = df[(df["Product_Code"] == selected["Product_Code"]) & (df["Warehouse"] == selected["Warehouse"])].copy()
# Daily totals preserve zero-demand days in the observed date span.
daily = series.groupby("Date")["Order_Demand"].sum().sort_index()
if len(daily) >= 2:
    calendar = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(calendar, fill_value=0)
mean_daily = float(daily.mean()) if len(daily) else 0.0
std_daily = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
z = {0.90: 1.2816, 0.95: 1.6449, 0.975: 1.96, 0.99: 2.3263}[service]
protection_days = lead_days + review_days
safety_stock = z * std_daily * np.sqrt(protection_days)
reorder_point = mean_daily * lead_days + z * std_daily * np.sqrt(lead_days)
target_stock = mean_daily * protection_days + safety_stock
recommended_order = max(0, int(np.ceil(target_stock - current_stock)))

m1, m2, m3, m4 = st.columns(4)
m1.metric("Average daily demand", f"{mean_daily:,.2f}")
m2.metric("Safety stock estimate", f"{safety_stock:,.0f}")
m3.metric("Reorder point", f"{reorder_point:,.0f}")
m4.metric("Suggested order quantity", f"{recommended_order:,}")
plan = pd.DataFrame([{
    "Product_Code": selected["Product_Code"], "Warehouse": selected["Warehouse"],
    "Product_Category": selected["Product_Category"], "Historical_Days": len(daily),
    "Average_Daily_Demand": mean_daily, "Daily_Demand_StdDev": std_daily,
    "Lead_Time_Days": lead_days, "Review_Period_Days": review_days,
    "Service_Level": service, "Estimated_Safety_Stock": int(np.ceil(safety_stock)),
    "Estimated_Reorder_Point": int(np.ceil(reorder_point)),
    "Estimated_Target_Stock": int(np.ceil(target_stock)), "Current_Stock_Input": current_stock,
    "Suggested_Order_Quantity": recommended_order
}])
st.dataframe(plan, use_container_width=True, hide_index=True)
st.download_button("Download this replenishment plan (CSV)", plan.to_csv(index=False).encode("utf-8"), file_name="replenishment_plan.csv", mime="text/csv")

st.subheader("Data quality notes")
missing_dates = int(df["Date"].isna().sum()) if "Date" in df.columns else 0
st.write(f"The app excludes rows with invalid/missing dates or invalid demand values after parsing. The source contains {len(df):,} usable rows after cleaning. Date gaps are not interpreted as confirmed zero demand except within the selected product/warehouse's observed date span; this assumption can affect variability and safety-stock estimates.")
with st.expander("Method and limitations"):
    st.markdown("""
    - **Demand basis:** historical order quantities aggregated by date, product, and warehouse.
    - **Reorder point:** average daily demand × lead time + a variability-based safety-stock buffer.
    - **Target stock:** average daily demand × (lead time + review period) + safety stock.
    - **Assumptions:** demand is treated as a daily series; the selected service level is approximated with a normal z-score; missing dates inside the observed span are treated as zero-demand days.
    - **Not available in the source:** purchase price, ordering cost, holding cost, supplier constraints, on-hand inventory, backorders, and warehouse capacity. Therefore the dashboard does not claim monetary profit or a globally optimal cost-minimizing order plan.
    - **Next step for a true cost optimization model:** provide these business inputs and define whether decisions are per product, warehouse, and replenishment period.
    """)

st.caption("Built with Streamlit, Pandas, NumPy and Plotly • Historical analysis is not a demand forecast.")
