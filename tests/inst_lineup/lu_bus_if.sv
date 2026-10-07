// Interface for the instance lineup goldens (not a golden itself).
interface lu_bus_if;
   logic [31:0] addr;
   logic        req;
   modport mst (output addr, output req);
   modport slv (input addr, input req);
endinterface
