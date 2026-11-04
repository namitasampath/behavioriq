# Stage 8 - Contextual bandit (simulated, 30,000 decisions on test sessions)

|                                             |   total_reward |   mean_reward_per_decision |   mean_reward_last_10k |   regret_vs_oracle |
|:--------------------------------------------|---------------:|---------------------------:|-----------------------:|-------------------:|
| oracle (expected)                           |       11113.6  |                     0.3705 |                 0.3668 |                0   |
| LinUCB                                      |        9348.08 |                     0.3116 |                 0.3132 |             1765.5 |
| Thompson Sampling                           |        9218.16 |                     0.3073 |                 0.3103 |             1895.4 |
| Random                                      |        8461.48 |                     0.282  |                 0.2775 |             2652.1 |
| Static rule (discount if predicted abandon) |        7953.68 |                     0.2651 |                 0.2582 |             3159.9 |
| Always none                                 |        7645    |                     0.2548 |                 0.2477 |             3468.6 |

Thompson Sampling permanently overtakes the static rule after **50 decisions** (warm-up window).

## Thompson Sampling action share per 3,000-decision window

|   window |   none |   discount_nudge |   compare_helper |   social_proof |
|---------:|-------:|-----------------:|-----------------:|---------------:|
|        0 |  0.248 |            0.211 |            0.376 |          0.165 |
|        1 |  0.128 |            0.163 |            0.54  |          0.169 |
|        2 |  0.061 |            0.136 |            0.576 |          0.227 |
|        3 |  0.047 |            0.08  |            0.65  |          0.224 |
|        4 |  0.045 |            0.059 |            0.699 |          0.197 |
|        5 |  0.063 |            0.045 |            0.66  |          0.232 |
|        6 |  0.072 |            0.041 |            0.652 |          0.235 |
|        7 |  0.068 |            0.041 |            0.66  |          0.231 |
|        8 |  0.047 |            0.033 |            0.643 |          0.277 |
|        9 |  0.031 |            0.048 |            0.682 |          0.239 |

Figures: bandit_reward.png, bandit_actions.png. Reward = purchase (0/1) minus action cost; response model in biq/bandit.py (UPLIFT, COST).
