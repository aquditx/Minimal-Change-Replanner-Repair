# Demo outputs

## REAL WK41 replay: Tuesday 07:00, IMP07 breakdown (36 h)
Disruption: IMP07 down for 36h from t=103h

Impact if nothing is done: 2 late order(s), 405,786 late pieces.

- Order 2100001752 (34.0BC1EC-W2-S, priority 1) will be 17.0 h late (due end of Fri) because IMP07 is down for 36 h from Tue 07:00, which stops its run. Cheapest fix: move the 546,480 pcs of 2100001752 from IMP07 to IMP01 (mold M:34.0BC1EC-W2-S) - 1 plan change (4100021974 finishes 41.1 h later (still on time); 1 other order(s) remain late).
- Order 2100001751 (34.0BC1EC-CL2-S, priority 1) will be 5.7 h late (due end of Thu) because IMP07 is down for 36 h from Tue 07:00, which stops its run. Cheapest fix: move the 620,075 pcs of 2100001751 from IMP07 to IMP01 (mold M:34.0BC1EC-CL2-S) - 1 plan change (4100021974 finishes 42.7 h later (still on time)).

Repaired plan (minimal change):
  1. Move 2100001751 (620,075 pcs) from IMP07 to IMP01 (mold M:34.0BC1EC-CL2-S), start Tue 15:00

               method  late_orders  late_qty  plan_changes  runtime_s
           do_nothing            2    405786             1       0.00
          manual_rule            0         0             2       0.00
         dispatch_edd            0         0             5       0.00
         dispatch_atc            0         0             4       0.00
         full_resolve            0         0             1       0.07
minimal_change_repair            0         0             1       0.33
         exact_repair            0         0             1       0.01

Due-date calibration notes: 4100021999: due moved 96h -> 143h (plan completes at 131h)

## Synthetic 10 machines, machine_down
Disruption: SIP02 down for 23h from t=25h

Impact if nothing is done: 1 late order(s), 354,508 late pieces.

- Order O023 (P001-PC, priority 3) will be 9.0 h late (due Mon 18:24) because SIP02 is down for 23 h from Sat 00:54, which stops its run. Cheapest fix: run O023 on SIP02 ahead of O010 - 3 plan changes (O010 finishes 38.0 h later (still on time)).

Repaired plan (minimal change):
  1. Re-time O003 on SIP01: start Tue 11:10 (was Mon 12:29)
  2. Re-time O023 on SIP02: start Sat 04:54 (was Sat 14:45)
  3. Re-time O016 on SIP02: start Tue 07:10 (was Mon 14:59)

               method  late_orders  late_qty  plan_changes  runtime_s
           do_nothing            1    354508             3       0.00
          manual_rule            3   2698281             3       0.00
         dispatch_edd            7   7462873            17       0.00
         dispatch_atc           12  19735722            17       0.00
         full_resolve            2    914900             3       0.41
minimal_change_repair            0         0             3       3.03
         exact_repair            2    697769             3       0.31

## Synthetic 10 machines, cavity_block
Disruption: 6 of 16 cavities blocked on mold M010 for 37h

Impact if nothing is done: 1 late order(s), 70,789 late pieces.

- Order O011 (P005-PC, priority 3) will be 1.3 h late (due Wed 02:24) because it waits behind a run slowed by 6/16 blocked cavities on mold M010. Cheapest fix: run O011 on IMP03 ahead of O007 - 3 plan changes (O007 finishes 27.3 h later (still on time)).

Repaired plan (minimal change):
  1. Re-time O007 on IMP03: start Mon 13:14 (was Sun 08:00)
  2. Re-time O011 on IMP03: start Sun 09:58 (was Mon 22:45)
  3. Re-time O013 on IMP03: start Wed 10:30 (was Wed 09:00)

               method  late_orders  late_qty  plan_changes  runtime_s
           do_nothing            1     70789             3       0.00
          manual_rule            1     70789             3       0.00
         dispatch_edd            3   2856713             6       0.00
         dispatch_atc            4   3776813             7       0.00
         full_resolve            2    622567             8       0.90
minimal_change_repair            0         0             3       1.49
         exact_repair            1     70789             3       0.25

## Synthetic 10 machines, mold_unavailable
Disruption: mold M001 unavailable for 33h

Impact if nothing is done: 2 late order(s), 1,086,691 late pieces.

- Order O023 (P001-PC, priority 3) will be 19.4 h late (due Mon 18:24) because mold M001 is unavailable for 33 h from Sun 00:42. No single-lot change gets it on time; it needs several moves together (see the repair plan).
- Order O003 (P001-PC, priority 2) will be 9.4 h late (due Wed 18:12) because mold M001 is unavailable for 33 h from Sun 00:42. Cheapest fix: move the 1,032,300 pcs of O003 from SIP01 to SIP02 (mold M001) - 2 plan changes (O016 finishes 26.2 h later (becomes 9.8 h late); 2 other order(s) remain late).

Repaired plan (minimal change):
  1. Move O003 (1,032,300 pcs) from SIP01 to SIP02 (mold M001), start Tue 13:50
  2. Move O016 (813,100 pcs) from SIP02 to SIP01 (mold M008), start Mon 18:14

               method  late_orders  late_qty  plan_changes  runtime_s
           do_nothing            2   1086691             2       0.00
          manual_rule            1   1102342             0       0.00
         dispatch_edd            4   3620795             6       0.00
         dispatch_atc            5   4540895             7       0.00
         full_resolve            1    764082             2       0.14
minimal_change_repair            1    764082             2       1.40
         exact_repair            1    764082             2       0.23

## Synthetic 10 machines, rush_order
Disruption: rush order RUSH003 (108,200 pcs, due t=91h)

Impact if nothing is done: 1 late order(s), 108,200 late pieces.

- Order RUSH003 (P007-BC, priority 3) will be 118.3 h late (due Mon 19:06) because the rush order arrives at Sun 00:48 and every suitable machine is busy. Cheapest fix: move the 108,200 pcs of RUSH003 from IMP01 to IMP04 (mold M007) - 2 plan changes (O001 finishes 7.4 h later (becomes 1.4 h late); O024 finishes 7.4 h later (still on time); 1 other order(s) remain late).

Repaired plan (minimal change):
  1. Re-time O024 on IMP04: start Mon 11:18 (was Mon 03:55)
  2. Re-time O001 on IMP04: start Thu 02:03 (was Wed 18:40)
  3. Move RUSH003 (108,200 pcs) from IMP01 to IMP04 (mold M007), start Mon 03:55

               method  late_orders  late_qty  plan_changes  runtime_s
           do_nothing            1    108200             0       0.00
          manual_rule            1    502872             2       0.00
         dispatch_edd            4   3472527            12       0.00
         dispatch_atc            5   3863581             9       0.00
         full_resolve            1     20259             4       0.73
minimal_change_repair            1     20259             2       2.01
         exact_repair            1     20259             2       0.33

## Synthetic 10 machines, quality_hold
Disruption: quality hold on 305,900 pcs of order O006

Impact if nothing is done: 1 late order(s), 305,900 late pieces.

- Order O006 (P011-PC, priority 1) will be 46.8 h late (due Mon 07:30) because 305,900 pcs are on quality hold; the sorter (queue, shift hours, 92% yield) returns only 281,428 good pcs and 24,472 pcs must be re-made. The sorter queue, not production, is the bottleneck: even with overtime it is still 41.0 h late. It needs more sorting capacity or a customer extension.

Repaired plan (minimal change):
  1. No changes needed: the old plan still meets every due date.

               method  late_orders  late_qty  plan_changes  runtime_s
           do_nothing            1    305900             0       0.00
          manual_rule            1    281428             0       0.00
         dispatch_edd            3   1271716             5       0.00
         dispatch_atc            3   2067582             6       0.00
         full_resolve            1    281428             2       0.09
minimal_change_repair            1    281428             0       1.24
         exact_repair            1    281428             0       0.10
