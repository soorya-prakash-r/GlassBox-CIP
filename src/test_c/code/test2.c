#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static inline char* duplicate_string(const char* input) {
    if (!input) return NULL;

    size_t length = strlen(input);
    char* copy = (char*)malloc(length + 1);

    if (!copy) return NULL;

    strcpy(copy, input);
    return copy;
}

int main() {
    const char* original = "Hello, World!";
    char* copy = duplicate_string(original);

    if (copy) {
        printf("Original: %s\n", original);
        printf("Copy: %s\n", copy);
        free(copy);
    } else {
        printf("Failed to duplicate string.\n");
    }

    return 0;
}