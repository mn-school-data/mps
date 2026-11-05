# Where enrollment dropped, and what recovery could look like

Source: MDE October 1 enrollment files, district level, FY2008-FY2026 (fall 2007 to fall 2025), in `mde_grade_enrollment.csv`. Counts here are K-12 only; early childhood and pre-K are left out. FY2020 = fall 2019, the last count before the pandemic.

## Summary

- **Every grade got smaller, not just the early grades.** From fall 2019 to fall 2025, K-12 fell from 33,507 to 28,814 (-14%). Kindergarten fell 19%, grades 6-9 fell 16-24%, and only grade 12 held roughly level (-2%). Statewide K-12 fell 3% over the same years and St. Paul fell 10%.
- **Middle school took the largest hit**: grades 6-8 fell 20% (7,146 to 5,697), elementary K-5 13% (16,186 to 14,029), high school 9-12 11% (10,175 to 9,088).
- **The decline started before 2020.** K-12 peaked in fall 2015 (FY2016) at 35,782. Kindergarten had already fallen from 3,450 (fall 2012) to about 2,930 (fall 2016-2019), and MPS normally lost 3-6% of each elementary class every year, while statewide classes grew slightly year to year.
- **There were two separate shocks in 2020-2022.** (1) Kindergarten dropped by about 480 in fall 2020 (2,919 to 2,443) and never came back; it has stayed at 2,220-2,450 since. (2) In fall 2020 and especially fall 2021, students already enrolled left at every grade. Against pre-2020 grade-to-grade retention, about 840 extra students were missing in fall 2020, 1,890 in fall 2021 and 220 in fall 2022, spread across elementary, middle and high school.
- **The leaving has stopped.** Since fall 2023, classes have kept their size from grade to grade better than before 2020: elementary classes now hold steady (ratios near 1.00 against about 0.96 before), and 9th grade gains about 11% over the previous year's 8th grade, against 3% before. Fall 2024 (FY2025) showed gains in almost every grade; what drove them is an open question (school-level counts and MDE resident-district data for that year would help show where the added students came from).
- **The one step that did not change is the move to 6th grade**: MPS keeps about 89% of 5th graders into 6th grade, the same as before 2020. St. Paul has a similar 6th-grade drop.
- **So most of the lasting loss is smaller kindergarten classes.** Holding pre-2020 retention fixed, the smaller kindergarten classes since fall 2020 account for about 2,950 fewer students in fall 2025. The extra departures of 2020-2022 have been roughly offset by the better retention since (actual fall 2025 is about 500 above that counterfactual). About 2,250 of the drop was already built in before 2020 (the trend if kindergarten and retention had stayed at their fall 2019 levels).
- **Part of the kindergarten drop is demographic.** Statewide kindergarten fell 13% from fall 2019 to fall 2025. MPS had 4.46% of the state's kindergartners in fall 2019; at that share it would have about 2,540 now, against 2,369 actual. So roughly 380 of the 550 missing kindergartners track the statewide decline and about 170 reflect MPS losing share. Minneapolis births fell 21.4% from 2008 to 2024, per the district (Oct 5, 2026 school transformation data post).

## Grade-to-grade retention

Ratio of a grade's count to the previous grade's count one year earlier, averaged. Above 1 means the class grew as it moved up.

| Transition | MPS pre-2020 (FY2016-20) | MPS 2021-2023 | MPS since (FY2024-26) | St. Paul since | Statewide since |
|---|---|---|---|---|---|
| K to 1 | 0.97 | 0.94 | 1.01 | 1.01 | 1.01 |
| 1 to 2 | 0.96 | 0.92 | 1.00 | 0.99 | 1.01 |
| 2 to 3 | 0.97 | 0.92 | 0.99 | 1.00 | 1.01 |
| 3 to 4 | 0.94 | 0.91 | 1.00 | 1.00 | 1.01 |
| 4 to 5 | 0.95 | 0.92 | 1.00 | 0.99 | 1.01 |
| 5 to 6 | 0.89 | 0.85 | 0.89 | 0.92 | 1.01 |
| 6 to 7 | 0.97 | 0.91 | 1.00 | 1.01 | 1.02 |
| 7 to 8 | 0.98 | 0.94 | 1.01 | 0.99 | 1.01 |
| 8 to 9 | 1.03 | 1.00 | 1.11 | 1.18 | 1.04 |
| 9 to 10 | 1.01 | 1.00 | 1.04 | 0.96 | 1.01 |
| 10 to 11 | 0.98 | 0.96 | 1.00 | 0.97 | 1.00 |
| 11 to 12 | 1.21 | 1.19 | 1.25 | 1.17 | 1.09 |

The 11-to-12 ratio is above 1 everywhere because 12th grade also counts students taking more than four years to finish. Full table by year: `grade_progression.csv`.

## Recovery scenarios

These are our own projections, not district forecasts, and every number in them follows from the assumptions stated here.

**How they work.** Cohort survival, the same basic method MPS uses for its one-year budget projections (Proposed FY2026 Budget, p.24). Start from the fall 2025 count in each grade. Each year, every class moves up one grade and is multiplied by that grade step's ratio (from the chart "Where classes lose and gain students"), and a new kindergarten class enters. Thirteen years (FY2027-FY2039) carries one kindergarten class all the way through grade 12.

**What every scenario assumes:**
- **Births keep falling and do not rebound.** Minnesota births fell 10.9% from 2016 to 2024 (69,746 to 62,128), about 1.4% a year. Statewide kindergarten is assumed to fall 1.4% a year from fall 2025 (56,993), reaching about 53,900 in FY2030 and 47,400 in FY2039.
- **MPS kindergarten is a share of statewide kindergarten.** The share measures how many families choose MPS at kindergarten (it also moves if Minneapolis' births change faster or slower than the state's). It was 5.12% in fall 2013, 4.56% on average in fall 2016-2019, 3.74% at its low in fall 2023 and 4.16% in fall 2025.

**What the scenarios vary: family choices at the points where the data shows families come and go.** These are kindergarten entry, the move into 6th grade (MPS loses about 11% of each class, much more than any other step) and the move into 9th grade (MPS gains about 11%). Other grade steps are now close to zero, so they are held at their FY2024-FY2026 averages, except in the downside case. The move into 7th grade shows no loss in MPS, because MPS middle school starts at 6th.

| Scenario | Exact assumption | K-12 FY2030 | FY2033 | FY2039 |
|---|---|---|---|---|
| Choices stay as now | MPS keeps 4.16% of Minnesota kindergartners; every grade step stays at its FY2024-FY2026 average | 29,534 | 29,745 | 28,200 |
| Half way back at K | The kindergarten share rises over three years to 4.36%, half way back to the FY2017-FY2020 average | 29,867 | 30,393 | 29,429 |
| Fully back at K | The kindergarten share rises over three years to 4.56%, the FY2017-FY2020 average | 30,200 | 31,041 | 30,658 |
| Fewer leave at 6th | As now, except the class moving into 6th keeps 94% of its students instead of 89% (half of today's loss kept) | 30,080 | 30,801 | 29,227 |
| More join at 9th | As now, except 9th grade gains 18% over the previous year's 8th grade instead of 11% (St. Paul's FY2024-FY2026 average) | 30,119 | 30,372 | 28,830 |
| All three gains | Kindergarten share half way back, half the 6th-grade loss kept, and 9th grade matching St. Paul | 31,007 | 32,119 | 31,191 |
| Leaving like pre-2020 | Kindergarten share as now, but every grade step returns to its FY2016-FY2020 average, when MPS lost 3-6% of each elementary class a year | 26,299 | 25,048 | 22,446 |
| District +10% target | The district's +10% elementary growth, taken as given: the kindergarten share rises over three years to 4.97% (last seen in fall 2014); other steps as now | 30,874 | 32,352 | 33,145 |

Fall 2019 K-12 was 33,507 and fall 2025 was 28,814. Results by level and year: `recovery_scenarios.csv`.

**What comes back on its own.** In "Choices stay as now", enrollment rises by about 900, to 29,745 in FY2033. This happens because the classes that lost students in 2020-2022 graduate, while classes now keep their size as they move up (see "Where classes lose and gain students"). After FY2033 the falling birth numbers take over, and enrollment drops to about 28,200 by FY2039.

**No scenario gets back to fall 2019.** With births falling, even the combined case peaks at about 32,100 (FY2033). The largest single lever is whether families keep staying from grade to grade: going back to pre-2020 leaving costs about 5,800 students by FY2039 compared with "Choices stay as now". Each of the three gains is worth about 600-2,500 students. K-12 in FY2039:

| Kindergarten share | Grade steps as now (FY2024-26) | Grade steps as before 2020 (FY2016-20) |
|---|---|---|
| As now (4.16%) | 28,200 | 22,446 |
| Half way back (4.36%) | 29,429 | 23,441 |
| Fully back (4.56%) | 30,658 | 24,436 |

## The district's +10% elementary projection

The district's Oct 5, 2026 school transformation post says: "Overall, we project elementary enrollment to increase by about ~10%, growing from 9,700 students today to 10,600 students by 2035-36."

**What the district is planning for.** The post's map shows "an independent demographer's prediction of where students will attend MPS schools in the future if we plan for 10% enrollment growth in 10 years across the district". The demographer spread that growth across the city, from about 20% in the North pathway to 4-8% in Camden, Roosevelt, Southwest and Washburn, and "MPS information about recent school choice was layered in". The stated aim behind the growth is attracting back resident children who now attend charter, other-district or private schools. The 9,700 counts elementary students in community schools; magnet schools add about 3,550, projected to grow to 3,900, also about +10%.

**Taking the target as given.** We added it as a scenario, "District +10% target", and asked what our model needs for it to happen. Elementary (K-5, MDE count) is 14,029 in fall 2025, so +10% is about 15,430 in fall 2035. With births falling 1.4% a year and today's grade steps, MPS would enroll about 4.97% of Minnesota's kindergartners from fall 2029 on, up from 4.16% now. MPS last had that share in fall 2014. Kindergarten classes would be about 2,640 in fall 2030 and 2,460 in fall 2035, against 2,369 in fall 2025.

In Minneapolis births, the same post reports 6,101 (2010), 6,049 (2011), 5,294 (2020) and 4,949 (2021). MPS kindergarten in fall 2025 was 45% of the Minneapolis babies born in 2020 (2,369 of 5,294), and 51% in fall 2015 (3,134 of 6,101). If Minneapolis births keep falling about 1.4% a year after 2021, the target corresponds to MPS enrolling about 56% of them.

**How our scenarios compare.** Elementary K-5 in fall 2035 (FY2036):

| Scenario | K-5 FY2036 | Change from fall 2025 |
|---|---|---|
| Choices stay as now | 12,918 | -8% |
| Half way back at K | 13,542 | -3% |
| Fully back at K | 14,167 | +1% |
| Fewer leave at 6th / More join at 9th | 12,918 | -8% |
| All three gains | 13,542 | -3% |
| Leaving like pre-2020 | 11,557 | -18% |
| District target | 15,432 | +10% |

The target sits above our other scenarios, which only change choices back toward recent levels. Changes at 6th and 9th grade do not move elementary at all. For context, elementary fell 24% over the last ten years (18,469 in fall 2015 to 14,029 in fall 2025).

**Open questions to check.** The post does not spell out how the growth arrives, so these are things a curious reader would want to confirm, not conclusions:

- **Where the new students come from.** The aim is to win back residents in charter, other-district and private schools. How many resident children are in each today, by grade, and which are most likely to return? MDE open-enrollment and charter counts by home district would show this.
- **Which grades it lands in.** Is the growth expected mostly at kindergarten entry, or also from older children switching back? Our scenario puts it all at kindergarten, which is where the data shows families choosing.
- **What it assumes about births.** Does the demographer's work use a Minneapolis birth or child-population forecast? The post reports births through 2021; later Minneapolis births would show whether the pool of 5-year-olds keeps shrinking.
- **What the "recent school choice" layer is.** Which choice data was used, and does it reflect the post-2020 pattern or earlier years?
- **What would bring families back.** The post ties growth to the transformation plan. Which program, building or boundary changes are expected to drive it, and on what timeline?
- **How progress would show.** A kindergarten class near 2,600 in fall 2029 or 2030 would be on track for the target; fall 2026 and 2027 counts are the first checkpoints.

## Where families go

- **When:** The October counts show the size of each change but not where students go. The big moves are at kindergarten entry, into 6th grade and into 9th grade (chart above).
- **Where, for all grades together:** Among public-school students living in Minneapolis, MPS enrolled about 63% in 2019-20 and 58% in 2022-23. Charter schools went from about 26% to 28%, and other districts (open enrollment) from about 12% to 14%. In 2022-23 another 5,051 resident students were in private schools and 368 were homeschooled (MDE data as cited by Mpls School Voices).
- **Not yet retrieved:** MDE publishes open enrollment files and resident-district counts that could show charter and other-district moves by grade. They sit behind the same download block as the grade counts.

## Caveats

- October 1 counts by grade come from MDE; they differ slightly from the average daily membership used for state aid and in the district's annual reports.
- Recent grade-step ratios rest on three years, one of them (FY2025) unusually high. If they fall back toward pre-2020 levels, every scenario ends lower (see the table above).
- Births are a single statewide trend. Minneapolis' own births fell 21.4% from 2008 to 2024 (district figure), faster than the state's, which would lower MPS kindergarten further at a fixed share.
- The scenarios do not model housing, new charter schools or school closures, any of which could move the kindergarten share or the grade steps.

## Sources for the grade analysis

- MDE Data Reports and Analytics, Student > Enrollment, files "2025-26 Enrollment" back to "2013-14 Enrollment" (State/District/School/County) and "Enrollment by Gender/Ethnicity" FY2008-FY2013: https://pub.education.mn.gov/MDEAnalytics/DataTopic.jsp?TOPICID=2 . Each row of `mde_grade_enrollment.csv` carries its file's download link.
- MPS Proposed FY2026 Budget (June 3, 2025), p.24, cohort survival method: https://resources.finalsite.net/images/v1748991242/mplsk12mnus/qqduushxosl2qwtlfya3/ProposedFiscalYear2025-26Budget_06032025.pdf
- Minnesota births 2016 and 2024: Minnesota OESW, "2026 Vital Statistics: Births and Fertility" fact sheet, May 2026: https://www.oesw.mn.gov/info/factSheets/2026%20Vital%20Statistics.pdf
- Resident student shares (MPS, charters, open enrollment, private): Mpls School Voices, citing MDE: https://mplsschoolsvoices.news/posts/minneapolis-public-schools-says-fewer-kids-living-in-the-city-has-caused-enrollment-to-decline
- MPS, Oct 5, 2026 school transformation post, Demographics section (Minneapolis births, +10% elementary projection, demographer's method): https://www.mpschools.org/about-mps/school-transformation/post-details/~board/school-transformation/post/minneapolis-and-mps-demographic-facility-financial-data
