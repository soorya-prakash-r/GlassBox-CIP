#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PI 3.14159

// Function prototype
double calculate_area(double radius);

// Struct definition
typedef struct {
    char name[50];
    int age;
} Person;

// Static inline utility
static inline void* allocate_buffer(size_t size) {
    void *ptr = malloc(size);
    if (!ptr) return NULL;
    memset(ptr, 0, size);
    return ptr;
}

// Normal function
double calculate_area(double radius) {
    return PI * radius * radius;
}

// Function with struct parameter
void print_person(Person *p) {
    if (p == NULL) {
        printf("Invalid person\n");
        return;
    }

    printf("Name: %s\n", p->name);
    printf("Age: %d\n", p->age);
}

int main() {
    Person p;
    strcpy(p.name, "Adarsh");
    p.age = 21;

    print_person(&p);

    double area = calculate_area(5.0);
    printf("Area: %.2f\n", area);

    void *buffer = allocate_buffer(128);
    if (buffer) {
        free(buffer);
    }

    return 0;
}