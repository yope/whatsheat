
$fn = 60;
height = 32;
zoff = height / 2;
heightd = height + 1;
zoffd = (height + 1) / 2;

module walls() {
    difference() {
        cube([140, 140, height], center=true);
        //cube([128, 128, heightd], center=true);
        cube([130, 113, heightd], center=true);
        cube([113, 130, heightd], center=true);
        //translate([0, 0, (height - 2)/2])
         //   cube([138, 138, 3], center=true);
        for(i = [0:3]) {
            rotate([0, 0, 90 * i]) {
                translate([62.5, 62.5, 0]) {
                    cylinder(h=heightd, d=5, center=true);
                    translate([0, 0, -zoffd])
                        cylinder(h=3, d=8);
                }
            }
        }
    }
}

module spacer() {
    difference() {
        union() {
            translate([0, 0, 6 - zoff]) {
                cube([8, 134, 12], center=true);
                cube([134, 8, 12], center=true);
            }
            translate([0, 0, -zoff]) {
                cylinder(h = 4, d = 62);
                translate([0, 0, 1.5])
                    cylinder(h = height - 15, d1 = 3, d2 = 52);
            }
        }
        translate([0, 0, -zoffd-1]) {
            cylinder(h = 7, d = 52);
        }
        translate([0, 0, 4.5-zoffd]) {
            cylinder(h = height - 15, d1 = 3, d2 = 52);
        }
        cube([3, 125, height - 4], center=true);
        cube([125, 3, height - 4], center=true);
    }
}

walls();
spacer();