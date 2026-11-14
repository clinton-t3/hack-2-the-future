
import hashlib
import pandas as pd
import streamlit as st

# ==============================================================================
# PAGE CONFIGURATION
# ==============================================================================

st.set_page_config(
    page_title="Millennium Mutual Operations Command Center",
    layout="wide",
)

# Hide the Deploy button visually
st.markdown(
    """
    <style>
    [data-testid="stAppDeployButton"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛡️ Millennium Mutual: Operations & Risk Command Center")

st.markdown(
    "Interactive decision-support system monitoring claims bottlenecks, "
    "broker loss ratios, and automated workload balancing."
)


# ==============================================================================
# 1. FRONT-END FILE UPLOADER (SIDEBAR)
# ==============================================================================

st.sidebar.header("📁 Data Management")

uploaded_claims = st.sidebar.file_uploader(
    "Upload claims.csv",
    type=["csv"]
)

uploaded_policies = st.sidebar.file_uploader(
    "Upload policies.csv",
    type=["csv"]
)


@st.cache_data
def load_data(claims_file, policies_file):

    if claims_file is not None and policies_file is not None:
        claims = pd.read_csv(claims_file)
        policies = pd.read_csv(policies_file)

    else:
        # Fallback to local default files
        try:
            claims = pd.read_csv("claims.csv")
            policies = pd.read_csv("policies.csv")

        except FileNotFoundError:
            return None, None

    # Clean and transform data
    policies["annual_premium"] = pd.to_numeric(
        policies["annual_premium"],
        errors="coerce"
    )

    claims["paid_amount"] = pd.to_numeric(
        claims["paid_amount"],
        errors="coerce"
    )

    claims["reported_date"] = pd.to_datetime(
        claims["reported_date"],
        format="mixed",
        errors="coerce"
    )

    claims["closed_date"] = pd.to_datetime(
        claims["closed_date"],
        format="mixed",
        errors="coerce"
    )

    claims["processing_days"] = (
        claims["closed_date"] - claims["reported_date"]
    ).dt.days

    return claims, policies


claims_df, policies_df = load_data(
    uploaded_claims,
    uploaded_policies
)

if claims_df is None or policies_df is None:

    st.warning(
        "⚠️ Please upload both claims.csv and policies.csv "
        "in the sidebar to begin."
    )

    st.stop()


# ==============================================================================
# 2. SESSION STATE (PRESERVE REDISTRIBUTION)
# ==============================================================================

if uploaded_claims is not None and uploaded_policies is not None:

    dataset_key = (
        hashlib.md5(uploaded_claims.getvalue()).hexdigest()
        + hashlib.md5(uploaded_policies.getvalue()).hexdigest()
    )

else:
    dataset_key = "local-default"


# Initialize working dataset when the source files change
if st.session_state.get("dataset_key") != dataset_key:

    st.session_state.dataset_key = dataset_key

    st.session_state.claims_df = claims_df.copy()

    st.session_state.last_redistributed = pd.DataFrame()

    st.session_state.redistribution_status = None


# Always use the working DataFrame
claims_df = st.session_state.claims_df


# ==============================================================================
# 3. HELPER FUNCTION: CALCULATE TEAM BACKLOG
# ==============================================================================

def calculate_team_stats(claims):

    team_stats = (
        claims.groupby("adjuster_team")
        .agg(
            total_claims=("claim_id", "count"),

            unclosed_backlog=(
                "closed_date",
                lambda x: x.isna().sum()
            ),

            avg_days=("processing_days", "mean")
        )
        .reset_index()
    )

    return team_stats


# ==============================================================================
# 4. AUTOMATED REDISTRIBUTION FUNCTION
# ==============================================================================

def redistribute_claims():

    # Get current working dataset
    claims = st.session_state.claims_df

    # Identify all available adjuster teams
    teams = sorted(
        claims["adjuster_team"].dropna().unique(),
        key=str
    )

    # Require at least two teams
    if len(teams) < 2:

        st.session_state.last_redistributed = pd.DataFrame()

        st.session_state.redistribution_status = (
            "At least two adjuster teams are required for redistribution."
        )

        return

    # Identify all unclosed claims
    open_mask = (
        claims["closed_date"].isna()
        & claims["adjuster_team"].notna()
    )

    open_claims = claims[open_mask].copy()

    total_open_claims = len(open_claims)

    if total_open_claims == 0:

        st.session_state.last_redistributed = pd.DataFrame()

        st.session_state.redistribution_status = (
            "There are no unclosed claims to redistribute."
        )

        return

    # --------------------------------------------------------------------------
    # STEP 1: CALCULATE THE TARGET NUMBER OF CLAIMS PER TEAM
    # --------------------------------------------------------------------------

    number_of_teams = len(teams)

    base_target = total_open_claims // number_of_teams

    remainder = total_open_claims % number_of_teams

    # Distribute any remaining claims one at a time
    # to ensure the difference between teams is at most one.
    target_counts = {}

    for i, team in enumerate(teams):

        target_counts[team] = (
            base_target + (1 if i < remainder else 0)
        )

    # --------------------------------------------------------------------------
    # STEP 2: CALCULATE CURRENT NUMBER OF CLAIMS PER TEAM
    # --------------------------------------------------------------------------

    current_counts = (
        open_claims.groupby("adjuster_team")
        .size()
        .to_dict()
    )

    # --------------------------------------------------------------------------
    # STEP 3: IDENTIFY CLAIMS THAT NEED TO BE MOVED
    # --------------------------------------------------------------------------

    claims_to_move = []

    receiving_teams = []

    for team in teams:

        current_count = current_counts.get(team, 0)

        target_count = target_counts[team]

        # Identify excess claims
        excess = max(0, current_count - target_count)

        if excess > 0:

            # Select oldest unclosed claims first
            team_claims = (
                open_claims[
                    open_claims["adjuster_team"] == team
                ]
                .sort_values(
                    by="reported_date",
                    ascending=True,
                    na_position="last"
                )
            )

            excess_indices = team_claims.head(excess).index.tolist()

            claims_to_move.extend(excess_indices)

        # Identify teams that need additional claims
        deficit = max(0, target_count - current_count)

        receiving_teams.extend([team] * deficit)

    # --------------------------------------------------------------------------
    # STEP 4: REASSIGN CLAIMS TO TEAMS WITH LOWER BACKLOGS
    # --------------------------------------------------------------------------

    if not claims_to_move:

        st.session_state.last_redistributed = pd.DataFrame()

        st.session_state.redistribution_status = (
            "The claims are already distributed as equally as possible."
        )

        return

    # Preserve previous team assignments for reporting
    previous_teams = claims.loc[
        claims_to_move,
        "adjuster_team"
    ].copy()

    # Update actual DataFrame assignments
    claims.loc[
        claims_to_move,
        "adjuster_team"
    ] = receiving_teams

    # Save updated dataset in session state
    st.session_state.claims_df = claims

    # --------------------------------------------------------------------------
    # STEP 5: SAVE REDISTRIBUTION RESULTS
    # --------------------------------------------------------------------------

    redistributed = claims.loc[claims_to_move].copy()

    redistributed.insert(
        0,
        "Previous Team",
        previous_teams.values
    )

    redistributed.insert(
        1,
        "New Team",
        claims.loc[claims_to_move, "adjuster_team"].values
    )

    st.session_state.last_redistributed = redistributed

    st.session_state.redistribution_status = (
        f"Successfully redistributed {len(claims_to_move)} claims "
        f"across {number_of_teams} teams."
    )


# ==============================================================================
# 5. TABS FOR WORKFLOW NAVIGATION
# ==============================================================================

tab1, tab2, tab3 = st.tabs(
    [
        "📊 1. Insights & Visualizations",
        "🚨 2. Business Conditions",
        "⚙️ 3. Automation Hub (Redistribution)"
    ]
)


# ==============================================================================
# TAB 1: INSIGHTS & VISUALIZATIONS
# ==============================================================================

with tab1:

    st.header("Key Business Insights & Visualizations")

    col1, col2 = st.columns(2)

    # --------------------------------------------------------------------------
    # INSIGHT 1: RENEWAL RATE BY PROCESSING TIME
    # --------------------------------------------------------------------------

    with col1:

        st.subheader("Insight 1: The 30-Day Renewal Cliff")

        st.markdown(
            "**What happened:** When claims are processed quickly "
            "(under 30 days), customer renewal rates hold strong at "
            "**~79%**. However, when processing drags past 30 days, "
            "renewals plummet to **41.1%**."
        )

        merged_cp = pd.merge(
            claims_df,
            policies_df,
            on="policy_id",
            how="inner"
        )

        decided = merged_cp[
            merged_cp["renewal_status"].isin(
                ["Renewed", "Not Renewed", "Cancelled"]
            )
        ].copy()

        decided["is_renewed"] = (
            decided["renewal_status"] == "Renewed"
        )

        decided["over_30_days"] = (
            decided["processing_days"] > 30
        )

        chart_data = (
            decided.groupby("over_30_days")["is_renewed"]
            .mean()
            .reset_index()
        )

        chart_data["Delay Status"] = chart_data[
            "over_30_days"
        ].map(
            {
                False: "Fast / Under 30d",
                True: "Delayed (>30d)"
            }
        )

        chart_data["Renewal Rate (%)"] = (
            chart_data["is_renewed"] * 100
        )

        st.bar_chart(
            chart_data.set_index("Delay Status")[
                "Renewal Rate (%)"
            ],
            color="#3498db"
        )

    # --------------------------------------------------------------------------
    # INSIGHT 2: BROKER LOSS RATIO
    # --------------------------------------------------------------------------

    with col2:

        st.subheader("Insight 2: Broker B07 Loss Ratio Outlier")

        st.markdown(
            "**What happened:** While most brokers operate safely "
            "under 75%, **Broker B07** has a catastrophic "
            "**211.5% loss ratio**, collecting $231k in premiums "
            "but paying out $489k in claims."
        )

        policy_payouts = (
            claims_df.groupby("policy_id")["paid_amount"]
            .sum()
            .reset_index()
        )

        m = pd.merge(
            policies_df,
            policy_payouts,
            on="policy_id",
            how="left"
        ).fillna(
            {"paid_amount": 0}
        )

        b_stats = (
            m[m["channel"] == "Broker"]
            .groupby("broker_code")
            .agg(
                premium=("annual_premium", "sum"),
                paid=("paid_amount", "sum")
            )
            .reset_index()
        )

        b_stats["loss_ratio"] = (
            b_stats["paid"]
            / b_stats["premium"].replace(0, float("nan"))
        ) * 100

        b_stats = b_stats.sort_values(
            by="loss_ratio",
            ascending=False
        )

        st.bar_chart(
            b_stats.set_index("broker_code")["loss_ratio"],
            color="#e74c3c"
        )


# ==============================================================================
# TAB 2: BUSINESS CONDITIONS (BOTH DETECTIONS)
# ==============================================================================

with tab2:

    st.header("Automated System Dangers & Detections")

    st.markdown(
        "The system continuously evaluates data against "
        "hardcoded operational thresholds."
    )

    col1, col2 = st.columns(2)

    # --------------------------------------------------------------------------
    # CONDITION 1: OPERATIONAL BACKLOG
    # --------------------------------------------------------------------------

    with col1:

        st.subheader("Condition 1: Operational Backlog")

        team_stats = calculate_team_stats(claims_df)

        team_c_backlog = team_stats.loc[
            team_stats["adjuster_team"] == "Team C",
            "unclosed_backlog"
        ].sum()

        team_a_backlog = team_stats.loc[
            team_stats["adjuster_team"] == "Team A",
            "unclosed_backlog"
        ].sum()

        if team_c_backlog > (team_a_backlog * 3):

            st.error(
                f"🚨 **CONDITION 1 DETECTED:** Team C holds "
                f"**{team_c_backlog} unclosed claims** (>3x Team A). "
                f"Customer wait times are past the 30-day cliff."
            )

        else:

            st.success(
                "✅ Adjuster team backlogs are balanced."
            )

        st.markdown("**Adjuster Team Statistics**")

        st.dataframe(
            team_stats,
            use_container_width=True,
            hide_index=True
        )

    # --------------------------------------------------------------------------
    # CONDITION 2: BROKER LOSS RATIO RISK
    # --------------------------------------------------------------------------

    with col2:

        st.subheader("Condition 2: Underwriting Risk")

        policy_payouts = (
            claims_df.groupby("policy_id")["paid_amount"]
            .sum()
            .reset_index()
        )

        m = pd.merge(
            policies_df,
            policy_payouts,
            on="policy_id",
            how="left"
        ).fillna(
            {"paid_amount": 0}
        )

        broker_stats = (
            m[m["channel"] == "Broker"]
            .groupby("broker_code")
            .agg(
                premium=("annual_premium", "sum"),
                paid=("paid_amount", "sum")
            )
            .reset_index()
        )

        broker_stats["loss_ratio"] = (
            broker_stats["paid"]
            / broker_stats["premium"].replace(0, float("nan"))
        ) * 100

        toxic_brokers = broker_stats[
            broker_stats["loss_ratio"] > 100
        ]

        if not toxic_brokers.empty:

            for _, row in toxic_brokers.iterrows():

                st.error(
                    f"🚨 **CONDITION 2 DETECTED:** Broker "
                    f"**{row['broker_code']}** has a catastrophic "
                    f"loss ratio of **{row['loss_ratio']:.1f}%** "
                    f"(${row['paid']:,.0f} paid vs "
                    f"${row['premium']:,.0f} collected)."
                )

        else:

            st.success(
                "✅ All broker portfolios are operating profitably (<100%)."
            )

        st.markdown("**Broker Loss Ratio Statistics**")

        st.dataframe(
            broker_stats,
            use_container_width=True,
            hide_index=True
        )


# ==============================================================================
# TAB 3: AUTOMATION HUB (WORKLOAD REDISTRIBUTION)
# ==============================================================================

with tab3:

    st.header("⚙️ Automated Workload Redistribution System")

    st.markdown(
        "The system automatically balances unclosed claims "
        "across all adjuster teams to ensure a more equitable "
        "distribution of active workloads."
    )

    # Calculate current team statistics
    current_team_stats = calculate_team_stats(claims_df)

    total_open = current_team_stats[
        "unclosed_backlog"
    ].sum()

    number_of_teams = claims_df[
        "adjuster_team"
    ].nunique()

    # Calculate expected target
    if number_of_teams > 0:

        target_per_team = total_open // number_of_teams

        st.info(
            f"**Current Status:** {total_open} unclosed claims "
            f"across {number_of_teams} adjuster teams. "
            f"The target is approximately {target_per_team} "
            f"claims per team."
        )

    # --------------------------------------------------------------------------
    # DISPLAY CURRENT WORKLOAD DISTRIBUTION
    # --------------------------------------------------------------------------

    st.subheader("📊 Current Team Workload Distribution")

    st.bar_chart(
        current_team_stats.set_index("adjuster_team")[
            "unclosed_backlog"
        ],
        color="#3498db"
    )

    st.dataframe(
        current_team_stats[
            ["adjuster_team", "total_claims", "unclosed_backlog"]
        ],
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------------------------
    # AUTOMATED REDISTRIBUTION BUTTON
    # --------------------------------------------------------------------------

    st.markdown("---")

    st.subheader("🚀 Automated Workload Redistribution")

    st.markdown(
        "Click the button below to automatically redistribute "
        "unclosed claims across all adjuster teams. "
        "The algorithm moves claims from teams with excess "
        "workloads to teams with lower workloads."
    )

    st.button(
        "🚀 Run Automated Workload Redistribution",
        type="primary",
        on_click=redistribute_claims
    )

    # --------------------------------------------------------------------------
    # DISPLAY REDISTRIBUTION RESULTS
    # --------------------------------------------------------------------------

    if st.session_state.redistribution_status is not None:

        if not st.session_state.last_redistributed.empty:

            redistributed = st.session_state.last_redistributed

            st.success(
                f"✅ {st.session_state.redistribution_status}"
            )

            st.markdown("### Redistribution Results")

            col1, col2 = st.columns(2)

            with col1:

                st.metric(
                    "Claims Redistributed",
                    len(redistributed)
                )

            with col2:

                st.metric(
                    "Teams Involved",
                    redistributed["New Team"].nunique()
                    + redistributed["Previous Team"].nunique()
                )

            st.markdown("### 📋 Claims Reassigned")

            st.markdown(
                "The following table shows which claims were moved "
                "and their previous and new team assignments."
            )

            display_columns = [
                "claim_id",
                "Previous Team",
                "New Team",
                "reported_date",
                "processing_days"
            ]

            st.dataframe(
                redistributed[
                    [
                        col for col in display_columns
                        if col in redistributed.columns
                    ]
                ],
                use_container_width=True,
                hide_index=True
            )

        else:

            st.info(
                st.session_state.redistribution_status
            )

    # --------------------------------------------------------------------------
    # DISPLAY UPDATED TEAM DISTRIBUTION
    # --------------------------------------------------------------------------

    st.markdown("---")

    st.subheader("📊 Updated Team Workload Distribution")

    updated_team_stats = calculate_team_stats(
        st.session_state.claims_df
    )

    st.bar_chart(
        updated_team_stats.set_index("adjuster_team")[
            "unclosed_backlog"
        ],
        color="#2ecc71"
    )

    st.dataframe(
        updated_team_stats,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------------------------
    # DISPLAY UPDATED CLAIMS DATASET
    # --------------------------------------------------------------------------

    st.markdown("---")

    st.subheader("📋 Updated Claims Dataset")

    st.markdown(
        "This table reflects the actual current team assignments "
        "after redistribution."
    )

    st.dataframe(
        st.session_state.claims_df,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------------------------
    # DOWNLOAD UPDATED CLAIMS DATASET
    # --------------------------------------------------------------------------

    csv_data = st.session_state.claims_df.to_csv(
        index=False
    ).encode("utf-8")

    st.download_button(
        label="📥 Download Updated Claims CSV",
        data=csv_data,
        file_name="updated_claims.csv",
        mime="text/csv"
    )
