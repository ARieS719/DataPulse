# 混沌拦截矩阵

- 生成时间：2026-11-04 02:46:21 · scale=medium · seed=42
- P0 拦截率/召回率：**1.0**
- 误报率：**0.0**

| 用例 | 组 | 通道 | 检出 | 期望 | 结果 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| duplicate_pk | positive | static | True | True | ✅ | {'detected': True, 'verdict': 'BLOCK', 'error': None} |
| duplicate_pk_clean | negative | static | False | False | ✅ | {'detected': False, 'verdict': 'PASS', 'error': None} |
| orphan_fk | positive | static | True | True | ✅ | {'detected': True, 'verdict': 'BLOCK', 'error': None} |
| orphan_fk_clean | negative | static | False | False | ✅ | {'detected': False, 'verdict': 'PASS', 'error': None} |
| amount_truncate | positive | static | True | True | ✅ | {'detected': True, 'verdict': 'BLOCK', 'error': None} |
| amount_truncate_clean | negative | static | False | False | ✅ | {'detected': False, 'verdict': 'PASS', 'error': None} |
| timestamp_mix | positive | static | True | True | ✅ | {'detected': True, 'verdict': 'BLOCK', 'error': None} |
| timestamp_mix_clean | negative | static | False | False | ✅ | {'detected': False, 'verdict': 'PASS', 'error': None} |
| schema_drift | positive | static | True | True | ✅ | {'detected': True, 'verdict': 'BLOCK', 'error': None} |
| schema_drift_clean | negative | static | False | False | ✅ | {'detected': False, 'verdict': 'PASS', 'error': None} |
| channel_drop | positive | volume | True | True | ✅ | {'detected': True, 'channel': '抖音', 'z': -5.157379, 'method': 'zscore', 'current_daily': 1065.5} |
| channel_drop_clean | negative | volume | False | False | ✅ | {'detected': False, 'channel': '京东', 'z': 0.0, 'method': 'zscore', 'current_daily': 1223.3} |
| psi_shift | positive | psi | True | True | ✅ | {'detected': True, 'psi': 10.0807} |
| psi_shift_clean | negative | psi | False | False | ✅ | {'detected': False, 'psi': 0.0} |
| semantic_pollution | positive | semantic | True | True | ✅ | {'detected': True, 'polluted': 10000, 'sampled': 200000} |
| semantic_pollution_clean | negative | semantic | False | False | ✅ | {'detected': False, 'polluted': 0, 'sampled': 200000} |

> 本矩阵由 scripts/run_chaos_experiment.py 自动生成，可复现（固定 seed + 副本隔离注入）。