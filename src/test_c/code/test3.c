// test_structs.c
#include <stdio.h>

typedef struct {
    int x, y;
    void (*callback)(int);
} Point;

static inline Point* create_point(int x, int y, void (*cb)(int)) {
    static Point p;
    p.x = x; p.y = y; p.callback = cb;
    return &p;
}

void print_coords(int val) { printf("Coord: %d\n", val); }

int main() {
    Point* pt = create_point(10, 20, print_coords);
    if (pt && pt->callback) pt->callback(42);
    return 0;
}
