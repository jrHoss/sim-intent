# Task 15 evaluation results

- Evaluation mode: **LIVE**
- Code revision: `7bd789c60d9b9e8b812b6fb7c0f29212587072e0+dirty`
- Configured model: `gpt-5.6-sol`
- Case manifest SHA-256: `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`
- Fixture hashes: `bracket.step=d81d158aa3b0a5464407496bd1782eba375f853e870fba6edd8cf485825f3c90`, `plate_hole.step=446cf12fed1139d2bfae5e483c1c34905b1444a8d05154a6bd972f1eaa214712`
- Score: **13/15**
- PASS: 12
- PASS_AFTER_CLARIFICATION: 1
- FAIL: 2
- 12/15 threshold achieved: **yes**
- Clarifications used: 1

| Case | Status | Expected IDs | Actual IDs | Expected type | Actual type | Expected normalized | Actual normalized | Clarification E/O | Failure | Export |
|---|---|---|---|---|---|---|---|---|---|---|
| bracket_bottom_fixed | PASS | `[[8]]` | `[[8]]` | `["fixed_displacement"]` | `["fixed_displacement"]` | `[{"components":["x","y","z"],"unit":"none"}]` | `[{"components":["x","y","z"],"unit":"none"}]` | no/no | - | - |
| bracket_vertical_click | FAIL | `[[5]]` | `[[5]]` | `["fixed_displacement"]` | `["fixed_displacement"]` | `[{"components":["y"],"unit":"none"}]` | `[{"components":["z"],"unit":"none"}]` | no/no | unit | - |
| ↳ bracket_vertical_click detail |  |  |  |  |  |  |  |  | Normalized components, vector, magnitude, or internal units differ. | - |
| bracket_bolt_holes_fixed | PASS | `[[11,12]]` | `[[11,12]]` | `["fixed_displacement"]` | `["fixed_displacement"]` | `[{"components":["x","y","z"],"unit":"none"}]` | `[{"components":["x","y","z"],"unit":"none"}]` | no/no | - | - |
| bracket_inner_pressure_clarify | PASS_AFTER_CLARIFICATION | `[[10]]` | `[[10]]` | `["pressure"]` | `["pressure"]` | `[{"magnitude":2.0,"unit":"MPa"}]` | `[{"magnitude":2.0,"unit":"MPa"}]` | yes/yes | - | - |
| bracket_top_force_5kn | PASS | `[[4]]` | `[[4]]` | `["resultant_surface_force"]` | `["resultant_surface_force"]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | no/no | - | - |
| bracket_gravity_neg_z | PASS | `[[]]` | `[[]]` | `["gravity"]` | `["gravity"]` | `[{"unit":"mm/s^2","vector":[0.0,0.0,-9810.0]}]` | `[{"unit":"mm/s^2","vector":[0.0,0.0,-9810.0]}]` | no/no | - | - |
| bracket_red_face_click | PASS | `[[1]]` | `[[1]]` | `["fixed_displacement"]` | `["fixed_displacement"]` | `[{"components":["x","y","z"],"unit":"none"}]` | `[{"components":["x","y","z"],"unit":"none"}]` | no/no | - | - |
| bracket_circled_holes_click | PASS | `[[11,12]]` | `[[11,12]]` | `["fixed_displacement"]` | `["fixed_displacement"]` | `[{"components":["x","y","z"],"unit":"none"}]` | `[{"components":["x","y","z"],"unit":"none"}]` | no/no | - | - |
| bracket_left_side_clarify | FAIL | `[[1]]` | `[]` | `["fixed_displacement"]` | `[]` | `[{"components":["x","y","z"],"unit":"none"}]` | `[]` | yes/no | ambiguity-unflagged | - |
| ↳ bracket_left_side_clarify detail |  |  |  |  |  |  |  |  | The system selected a region where the frozen case requires clarification. | - |
| plate_top_force_5000n | PASS | `[[3]]` | `[[3]]` | `["resultant_surface_force"]` | `["resultant_surface_force"]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | no/no | - | - |
| plate_top_force_5kn | PASS | `[[3]]` | `[[3]]` | `["resultant_surface_force"]` | `["resultant_surface_force"]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | no/no | - | - |
| plate_top_force_0_005mn | PASS | `[[3]]` | `[[3]]` | `["resultant_surface_force"]` | `["resultant_surface_force"]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | `[{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | no/no | - | - |
| plate_hole_pressure_pa | PASS | `[[7]]` | `[[7]]` | `["pressure"]` | `["pressure"]` | `[{"magnitude":2.0,"unit":"MPa"}]` | `[{"magnitude":2.0,"unit":"MPa"}]` | no/no | - | - |
| plate_hole_pressure_kpa | PASS | `[[7]]` | `[[7]]` | `["pressure"]` | `["pressure"]` | `[{"magnitude":2.0,"unit":"MPa"}]` | `[{"magnitude":2.0,"unit":"MPa"}]` | no/no | - | - |
| bracket_combined_export | PASS | `[[11,12],[4]]` | `[[11,12],[4]]` | `["fixed_displacement","resultant_surface_force"]` | `["fixed_displacement","resultant_surface_force"]` | `[{"components":["x","y","z"],"unit":"none"},{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | `[{"components":["x","y","z"],"unit":"none"},{"unit":"N","vector":[0.0,-5000.0,0.0]}]` | no/no | - | bracket_abaqus.py (b33921a554ce) |

## Known limitations

- No solver was executed and the Abaqus artifact was not run in Abaqus.
- Abaqus face ordering assumes OCC tag n maps to imported part.faces[n-1].
- Click evidence is supported; general screenshot or drawing recognition is not.
- No meshing, contact, nonlinear, thermal, dynamic, or result-validation workflow is included.
- The optional CalculiX live check requires an installed ccx executable.

Replay reports measure deterministic regression only and are never presented as live LLM performance.
