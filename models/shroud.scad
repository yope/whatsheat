
$fn = 60;
height = 32;
zoff = height / 2;
heightd = height + 1;
zoffd = (height + 1) / 2;

plateh = 3;
deltah = 0.1;
enddiam = 142;
wallt = 2;
wallt2 = 2 * wallt;

module plate() {
    difference() {
        cube([145, 145, plateh], center=true);
        for(i = [0:3]) {
            rotate([0, 0, 90 * i]) {
                translate([62.5, 62.5, 0]) {
                    cylinder(h=heightd, d=5, center=true);
                    translate([0, 0, -zoffd])
                        cylinder(h=3, d=8);
                }
            }
        }
        translate([0, 0, 0])
            cylinder(h=plateh+2*deltah, d=141, center=true);
        difference() {
            cylinder(d=210, h=plateh+2*deltah, center=true);
            cylinder(d=192, h=plateh+3*deltah, center=true);
        }
    }
}

module ring() {
    difference() {
        cylinder(d1=141 + wallt2, d2=enddiam, h=15);
        translate([0, 0, -deltah])
            cylinder(d1=141, d2=enddiam-wallt2, h=15+2*deltah);
    }
    translate([0, 0, 15]) {
        difference() {
            cylinder(d=enddiam, h=15);
            translate([0, 0, -deltah])
                cylinder(d=enddiam-wallt2, h=15+2*deltah);
        }
    }
}

plate();
ring();