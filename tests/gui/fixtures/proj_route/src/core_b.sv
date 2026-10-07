// coreB: ANSI AUTO-style wrapper around instE and instF.
// The wrapper-level annotation below connects two of its children: instE's e_busy
// drives instF's f_hold (a child port on the left side: INSTPATH:PORT).
module core_b
  (/*AUTOINPUT*/
   /*AUTOOUTPUT*/
   );

   /*AUTOWIRE*/

   leaf_e instE (/*AUTOINST*/);

   leaf_f instF (/*AUTOINST*/);

   //auto_route instE:e_busy :: to :: instF:f_hold
endmodule
