#include "4DPlugin-Running-Application.h"
#include "support/4DPlugin-JSON.h"
#include "C_BLOB.h"
#include "PrivateTypes.h"
#include "EntryPoints.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>

extern Call4DProcPtr gCall4D;
extern "C" FOURDCALL FourDPackex(PA_long32, void *, void **, void *);
static bool allocatedEmpty = true;
static int failHandleCountdown, failCommand, wrongCommand;
static bool failProperty, failHas, failString;
static int appCount = 3, operations;
static std::map<PA_Unichar *, size_t> strings;
static std::map<PA_Handle, size_t> handles;
struct Ref {
    int owners = 1;
    std::map<CUTF16String, PA_Variable> properties;
};
static std::set<Ref *> refs;
static std::set<void *> pictures;
static int stringCreates, stringDisposes, refCreates, refDisposes;
static int handleCreates, handleDisposes, pictureCreates, pictureDisposes;

static CUTF16String u16(const char *text)
{
    NSString *s = [NSString stringWithUTF8String:text];
    CUTF16String result([s length], 0);
    if(!result.empty())
        [s getCharacters:&result[0] range:NSMakeRange(0, [s length])];
    return result;
}
static CUTF8String u8(const char *text)
{
    return CUTF8String((const uint8_t *)text);
}
static PA_Unistring makeString(const CUTF16String& text)
{
    PA_Unistring result = {};
    result.fLength = (PA_long32)text.size();
    if(!text.empty() || allocatedEmpty)
    {
        result.fString = new PA_Unichar[text.size() + 1];
        std::copy(text.begin(), text.end(), result.fString);
        result.fString[text.size()] = 0;
        result.fReserved1 = (PA_long32)(text.size() + 1);
        PA_Unichar *pointer = result.fString;
        strings[pointer] = text.size() + 1;
        ++stringCreates;
    }
    return result;
}
static CUTF16String readString(PA_Unistring value)
{
    PA_Unichar *pointer = value.fString;
    if(!pointer)
    {
        assert(value.fLength == 0);
        return {};
    }
    assert(strings.count(pointer));
    assert(strings.at(pointer) == (size_t)value.fLength + 1);
    assert(value.fReserved1 == value.fLength + 1);
    assert(value.fString[value.fLength] == 0);
    return CUTF16String(value.fString, value.fLength);
}
static void disposeString(PA_Unistring value)
{
    PA_Unichar *pointer = value.fString;
    if(pointer)
    {
        assert(strings.erase(pointer) == 1);
        delete[] pointer;
        ++stringDisposes;
    }
}
static Ref *newRef()
{
    Ref *result = new Ref;
    refs.insert(result);
    ++refCreates;
    return result;
}
static void clear(PA_Variable& value);
static void dropRef(void *pointer)
{
    if(!pointer) return;
    Ref *ref = (Ref *)pointer;
    assert(refs.count(ref));
    assert(ref->owners > 0);
    if(--ref->owners == 0)
    {
        for(auto& property : ref->properties) clear(property.second);
        refs.erase(ref);
        delete ref;
        ++refDisposes;
    }
}
static PA_Variable clone(PA_Variable value)
{
    if(value.fType == eVK_Unistring)
        value.uValue.fString = makeString(readString(value.uValue.fString));
    else if(value.fType == eVK_Object || value.fType == eVK_Collection)
    {
        Ref *ref = (Ref *)value.uValue.fObject;
        if(ref) { assert(refs.count(ref)); ++ref->owners; }
    }
    else if(value.fType == eVK_Picture && value.uValue.fPicture)
    {
        void *copy = new char;
        pictures.insert(copy);
        value.uValue.fPicture = copy;
        ++pictureCreates;
    }
    return value;
}
static void clear(PA_Variable& value)
{
    assert(value.fType != 0); // failed-command raw output must never be adopted
    switch(value.fType)
    {
        case eVK_Unistring: disposeString(value.uValue.fString); break;
        case eVK_Object: case eVK_Collection: dropRef(value.uValue.fObject); break;
        case eVK_Picture:
            if(value.uValue.fPicture)
            {
                void *pointer = value.uValue.fPicture;
                assert(pictures.erase(pointer) == 1);
                delete (char *)value.uValue.fPicture;
                ++pictureDisposes;
            }
            break;
        case eVK_ArrayUnicode:
            for(int i = 0; i <= value.uValue.fArray.fNbElements; ++i)
                disposeString(((PA_Unistring *)*value.uValue.fArray.fData)[i]);
            PA_DisposeHandle(value.uValue.fArray.fData);
            break;
        case eVK_ArrayLongint: PA_DisposeHandle(value.uValue.fArray.fData); break;
        case eVK_Blob: PA_DisposeHandle(value.uValue.fBlob.fHandle); break;
        default: break;
    }
    value.fType = eVK_Undefined;
}
static PA_Variable textValue(const char *text)
{
    PA_Variable result = PA_CreateVariable(eVK_Undefined);
    PA_Unistring str = makeString(u16(text));
    PA_SetStringVariable(&result, &str);
    return result;
}
static void host(short selector, EngineBlock *eb)
{
    eb->fError = eER_NoErr;
    switch(selector)
    {
        case EX_CREATE_UNISTRING:
        {
            const PA_Unichar *text = (PA_Unichar *)eb->fHandle;
            eb->fUniString1 = makeString(text ? CUTF16String(text) : CUTF16String());
            break;
        }
        case EX_DISPOSE_UNISTRING: disposeString(eb->fUniString1); break;
        case EX_SET_UNISTRING:
        {
            CUTF16String text((PA_Unichar *)eb->fHandle);
            disposeString(eb->fUniString1);
            eb->fUniString1 = makeString(text);
            break;
        }
        case EX_VARIABLE_TO_STRING:
        {
            if(failString)
            {
                memset(&eb->fUniString1, 0xa5, sizeof(PA_Unistring));
                eb->fError = (PA_ErrorCode)1;
                break;
            }
            PA_Variable *value = (PA_Variable *)eb->fHandle;
            assert(value->fType == eVK_Unistring);
            readString(value->uValue.fString);
            eb->fUniString1 = value->uValue.fString;
            break;
        }
        case EX_CLEAR_VARIABLE: clear(*(PA_Variable *)eb->fPtr1); break;
        case EX_HANDLE_MANAGER:
        {
            PA_Handle handle = eb->fParam1 == 1 ? nullptr : eb->fHandle;
            sLONG_PTR length = eb->fParam1 == 1 || eb->fParam1 == 3 ? eb->fParam2 : 0;
            switch(eb->fParam1)
            {
                case 1:
                    if(failHandleCountdown && --failHandleCountdown == 0)
                    {
                        eb->fHandle = nullptr;
                        eb->fError = (PA_ErrorCode)1;
                        break;
                    }
                    handle = new char *;
                    *handle = (char *)calloc(std::max<sLONG_PTR>(1, length), 1);
                    handles[handle] = length;
                    eb->fHandle = handle;
                    ++handleCreates;
                    break;
                case 2:
                    assert(handles.erase(handle) == 1);
                    free(*eb->fHandle);
                    delete eb->fHandle;
                    ++handleDisposes;
                    break;
                case 3:
                    assert(handles.count(handle));
                    *eb->fHandle = (char *)realloc(*eb->fHandle, std::max<sLONG_PTR>(1, length));
                    handles[handle] = length;
                    break;
                case 4: eb->fParam2 = handles.at(handle); break;
                case 5: eb->fParam3 = (sLONG_PTR)*eb->fHandle; break;
                case 6: break;
                default: std::abort();
            }
            break;
        }
        case EX_GET_OBJ_VALUE:
        {
            if(failProperty)
            {
                memset(eb->fPtr2, 0xa5, sizeof(PA_Variable));
                eb->fError = (PA_ErrorCode)1;
                break;
            }
            Ref *ref = (Ref *)eb->fPtr1;
            assert(refs.count(ref));
            *(PA_Variable *)eb->fPtr2 = clone(ref->properties.at(readString(eb->fUniString1)));
            break;
        }
        case EX_SET_OBJ_VALUE:
        {
            Ref *ref = (Ref *)eb->fPtr1;
            assert(refs.count(ref));
            CUTF16String key = readString(eb->fUniString1);
            if(ref->properties.count(key)) clear(ref->properties.at(key));
            ref->properties[key] = clone(*(PA_Variable *)eb->fPtr2);
            break;
        }
        case EX_EXECUTE_COMMAND_BY_ID:
        {
            PA_Variable *out = (PA_Variable *)eb->fPtr2;
            if(eb->fParam1 == failCommand || (eb->fParam1 == 1231 && failHas))
            {
                memset(out, 0, sizeof(*out));
                eb->fError = (PA_ErrorCode)1;
                break;
            }
            if(eb->fParam1 == wrongCommand)
            {
                *out = textValue("wrong-kind");
                if(eb->fParam1 == 1217)
                {
                    clear(*out);
                    PA_SetObjectVariable(out, newRef());
                }
                break;
            }
            PA_Variable **args = (PA_Variable **)eb->fPtr1;
            if(eb->fParam1 == 1231)
            {
                Ref *ref = (Ref *)args[0]->uValue.fObject;
                PA_SetBooleanVariable(out, ref->properties.count(readString(args[1]->uValue.fString)) != 0);
            }
            else if(eb->fParam1 == 1225)
            {
                Ref *copy = newRef();
                for(auto& property : ((Ref *)args[0]->uValue.fObject)->properties)
                    copy->properties[property.first] = clone(property.second);
                PA_SetObjectVariable(out, copy);
            }
            else if(eb->fParam1 == 1217)
                *out = textValue("synthetic \xE6\x97\xA5 \xF0\x9F\x98\x80");
            else std::abort();
            break;
        }
        case EX_CREATE_PICTURE:
        {
            eb->fPicture = new char;
            void *pointer = eb->fPicture;
            pictures.insert(pointer);
            ++pictureCreates;
            break;
        }
        case EX_DISPOSE_PICTURE:
        {
            void *pointer = eb->fPicture;
            assert(pictures.erase(pointer) == 1);
            delete (char *)eb->fPicture;
            ++pictureDisposes;
            break;
        }
        default:
            std::cerr << "Unexpected SDK selector " << selector << "\n";
            std::abort();
    }
}

@implementation FixtureApplication
+ (id)currentApplication { return [[[self alloc] init] autorelease]; }
+ (NSArray *)runningApplicationsWithBundleIdentifier:(NSString *)identifier
{
    assert([identifier isEqualToString:@"fixture"] || [identifier isEqualToString:@"missing"]);
    return [identifier isEqualToString:@"missing"] ? @[] : @[[self currentApplication]];
}
- (NSString *)localizedName { return @"fixture \u65e5 \U0001f600"; }
- (NSString *)bundleIdentifier { return @"fixture"; }
- (int)processIdentifier { return 4242; }
- (BOOL)terminate { ++operations; return YES; }
- (BOOL)forceTerminate { ++operations; return YES; }
- (BOOL)isActive { return YES; }
- (BOOL)activateWithOptions:(NSUInteger)options { ++operations; return YES; }
- (id)icon { return [[[FixtureImage alloc] init] autorelease]; }
- (BOOL)hide { ++operations; return YES; }
- (BOOL)unhide { ++operations; return YES; }
- (BOOL)isHidden { return NO; }
- (NSURL *)bundleURL { return (NSURL *)@"/synthetic/fixture.app"; }
- (NSURL *)executableURL { return (NSURL *)@"/synthetic/fixture"; }
@end
@implementation FixtureWorkspace
+ (id)sharedWorkspace { return [[[self alloc] init] autorelease]; }
- (NSArray *)runningApplications
{
    NSMutableArray *result = [NSMutableArray array];
    for(int i = 0; i < appCount; ++i) [result addObject:[FixtureApplication currentApplication]];
    return result;
}
@end
@implementation FixtureBundle
+ (id)bundleWithIdentifier:(NSString *)identifier
{
    assert([identifier isEqualToString:@"fixture"]);
    return [[[self alloc] init] autorelease];
}
- (NSURL *)bundleURL { return (NSURL *)@"/synthetic/fixture.app"; }
@end
@implementation FixtureImage
- (CGImageRef)CGImageForProposedRect:(NSRect *)rect context:(id)context hints:(id)hints
{ return (CGImageRef)1; }
@end
CFURLRef FixtureURLCreate(CFAllocatorRef, CFStringRef path, CFURLPathStyle, Boolean)
{ return (CFURLRef)CFStringCreateCopy(kCFAllocatorDefault, path); }
CFStringRef FixtureURLCopyPath(CFURLRef path, CFURLPathStyle)
{ return CFStringCreateCopy(kCFAllocatorDefault, (CFStringRef)path); }
CGImageDestinationRef FixtureDestinationCreate(CFMutableDataRef data, CFStringRef, size_t, CFDictionaryRef)
{ return (CGImageDestinationRef)CFRetain(data); }
void FixtureDestinationAdd(CGImageDestinationRef destination, CGImageRef, CFDictionaryRef)
{
    const UInt8 bytes[] = {1, 2, 3};
    CFDataAppendBytes((CFMutableDataRef)destination, bytes, sizeof(bytes));
}
bool FixtureDestinationFinalize(CGImageDestinationRef) { return true; }

static void balanced()
{
    if(!strings.empty() || !handles.empty() || !refs.empty() || !pictures.empty())
    {
        std::cerr << "OWNER IMBALANCE strings=" << strings.size()
                  << " handles=" << handles.size() << " refs=" << refs.size()
                  << " pictures=" << pictures.size() << "\n";
        std::abort();
    }
}
static void runCase(const std::string& name)
{
    const char *fixtures[] = {"", "ASCII", "\xE6\x97\xA5", "\xF0\x9F\x98\x80"};
    if(name == "scalar-setters")
    {
        for(bool empty : {false, true})
        {
            allocatedEmpty = empty;
            Ref *object = newRef();
            for(const char *text : fixtures)
            {
                CUTF16String utf = u16(text);
                ob_set_s(object, "key", text);
                assert(readString(object->properties.at(u16("key")).uValue.fString) == utf);
                ob_set_s(object, L"key", text);
                assert(readString(object->properties.at(u16("key")).uValue.fString) == utf);
                ob_set_a(object, L"key", &utf);
                assert(readString(object->properties.at(u16("key")).uValue.fString) == utf);
            }
            ob_set_a(object, L"key", L"wide \u65e5 \U0001f600");
            assert(readString(object->properties.at(u16("key")).uValue.fString) == u16("wide \xE6\x97\xA5 \xF0\x9F\x98\x80"));
            dropRef(object);
            balanced();
        }
    }
    else if(name == "picture-setter")
    {
        Ref *object = newRef();
        PA_Picture picture = PA_CreatePicture(nullptr, 0);
        ob_set_p(object, L"key", picture); // consumes caller's owner
        void *stored = object->properties.at(u16("key")).uValue.fPicture;
        assert(pictures.count(stored));
        dropRef(object);
    }
    else if(name == "property-getters" || name == "property-mismatches")
    {
        for(bool empty : {false, true})
        {
            allocatedEmpty = empty;
            Ref *object = newRef();
            if(name == "property-getters")
            {
                for(const char *text : fixtures)
                {
                    object->properties[u16("key")] = textValue(text);
                    CUTF8String output;
                    assert(ob_get_a(object, L"key", &output));
                    assert(output == u8(text));
                    clear(object->properties.at(u16("key")));
                }
                PA_SetBooleanVariable(&object->properties[u16("key")], true);
                assert(ob_get_b(object, L"key"));
                PA_SetRealVariable(&object->properties[u16("key")], 42.5);
                assert(ob_get_n(object, L"key") == 42.5);
                for(bool collection : {false, true})
                {
                    Ref *child = newRef();
                    if(collection) PA_SetCollectionVariable(&object->properties[u16("key")], child);
                    else PA_SetObjectVariable(&object->properties[u16("key")], child);
                    void *returned = collection ? ob_get_c(object, L"key") : ob_get_o(object, L"key");
                    assert(returned == child && child->owners == 2);
                    dropRef(returned);
                    clear(object->properties.at(u16("key")));
                }
            }
            else
            {
                for(int kind = 0; kind < 4; ++kind)
                {
                    PA_Variable& value = object->properties[u16("key")];
                    value.fType = eVK_Undefined;
                    if(kind == 0) value = textValue("owned");
                    if(kind == 1) PA_SetPictureVariable(&value, PA_CreatePicture(nullptr, 0));
                    if(kind == 2) PA_SetObjectVariable(&value, newRef());
                    if(kind == 3) PA_SetCollectionVariable(&value, newRef());
                    assert(!ob_get_b(object, L"key"));
                    assert(ob_get_n(object, L"key") == 0);
                    CUTF8String output;
                    ob_get_a(object, L"key", &output);
                    if(kind != 2) assert(ob_get_o(object, L"key") == nullptr);
                    if(kind != 3) assert(ob_get_c(object, L"key") == nullptr);
                    clear(value);
                }
            }
            dropRef(object);
            balanced();
        }
    }
    else if(name == "property-errors")
    {
        Ref *object = newRef();
        object->properties[u16("key")] = textValue("owned");
        for(int mode = 0; mode < 3; ++mode)
        {
            failHas = mode == 0;
            failProperty = mode == 1;
            failString = mode == 2;
            for(int getter = 0; getter < (failString ? 1 : 5); ++getter)
            {
                bool threw = false;
                try {
                    CUTF8String output;
                    if(getter == 0) ob_get_a(object, L"key", &output);
                    if(getter == 1) ob_get_b(object, L"key");
                    if(getter == 2) ob_get_n(object, L"key");
                    if(getter == 3) ob_get_o(object, L"key");
                    if(getter == 4) ob_get_c(object, L"key");
                } catch(const std::runtime_error&) { threw = true; }
                assert(threw);
            }
        }
        failHas = failProperty = failString = false;
        dropRef(object);
    }
    else if(name == "stringify-errors" || name == "stringify-success")
    {
        Ref *object = newRef();
        if(name == "stringify-success")
        {
            CUTF8String output;
            ob_stringify(object, &output);
            assert(output == u8("synthetic \xE6\x97\xA5 \xF0\x9F\x98\x80"));
        }
        else
        {
            for(int failure = 0; failure < 7; ++failure)
            {
                failHandleCountdown = failure < 2 ? failure + 1 : 0;
                failCommand = failure == 2 ? 1225 : failure == 3 ? 1217 : 0;
                wrongCommand = failure == 4 ? 1225 : failure == 5 ? 1217 : 0;
                failString = failure == 6;
                bool threw = false;
                try {
                    CUTF8String output = u8("unchanged");
                    ob_stringify(object, &output);
                } catch(const std::runtime_error&) { threw = true; }
                assert(threw);
                failHandleCountdown = failCommand = wrongCommand = 0;
                failString = false;
            }
        }
        dropRef(object);
    }
    else if(name == "arrays")
    {
        for(bool empty : {false, true})
        {
            allocatedEmpty = empty;
            PA_Variable array = PA_CreateVariable(eVK_ArrayUnicode);
            BytePtr parameter[] = {(BytePtr)&array};
            ARRAY_TEXT source;
            for(const char *text : fixtures)
            {
                CUTF16String utf = u16(text);
                source.appendUTF16String(&utf);
            }
            for(int repeat = 0; repeat < 20; ++repeat)
            {
                source.toParamAtIndex(parameter, 1);
                assert(array.uValue.fArray.fNbElements == 3);
                std::set<PA_Unichar *> distinct;
                for(int i = 0; i < 4; ++i)
                {
                    PA_Unistring str = PA_GetStringInArray(array, i);
                    assert(readString(str) == u16(fixtures[i]));
                    PA_Unichar *pointer = str.fString;
                    if(pointer) assert(distinct.insert(pointer).second);
                }
                ARRAY_TEXT copied;
                copied.fromParamAtIndex(parameter, 1);
                PA_Unistring replacement = makeString(u16("replacement"));
                PA_SetStringInArray(array, 1, &replacement);
                CUTF16String original;
                copied.copyUTF16StringAtIndex(&original, 1);
                assert(original == u16("ASCII"));
            }
            for(int rejection = 0; rejection < 5; ++rejection)
            {
                PA_Variable target = array;
                int index = 0;
                if(rejection == 0) target.fType = eVK_Longint;
                if(rejection == 1) target.uValue.fArray.fData = nullptr;
                if(rejection == 2) index = -1;
                if(rejection == 3) index = 4;
                PA_Unistring owner = makeString(u16("rejected"));
                PA_SetStringInArray(target, index, rejection == 4 ? nullptr : &owner);
                assert(readString(owner) == u16("rejected"));
                PA_DisposeUnistring(&owner);
            }
            PA_ResizeArray(&array, 0);
            PA_ClearVariable(&array);
            balanced();
        }
    }
    else if(name == "text")
    {
        for(bool empty : {false, true})
        {
        allocatedEmpty = empty;
        for(const char *text : fixtures)
        {
            C_TEXT value;
            value.setUTF8String((const uint8_t *)text, (uint32_t)strlen(text));
            assert(CUTF16String(value.getUTF16StringPtr(), value.getUTF16Length()) == u16(text));
            CUTF8String output;
            value.copyUTF8String(&output);
            assert(output == u8(text));
            PA_Unistring destination = makeString(u16("old"));
            BytePtr parameter[] = {(BytePtr)&destination};
            value.toParamAtIndex(parameter, 1);
            assert(readString(destination) == u16(text));
            PA_Unistring *result = &destination;
            value.setReturn((sLONG_PTR *)&result);
            assert(readString(destination) == u16(text));
            PA_DisposeUnistring(&destination);
        }
        }
    }
    else if(name == "dispatcher")
    {
        for(int count : {0, 3})
        {
            appCount = count;
            PA_Variable a = PA_CreateVariable(eVK_ArrayUnicode);
            PA_Variable b = PA_CreateVariable(eVK_ArrayUnicode);
            PA_Variable c = PA_CreateVariable(eVK_ArrayLongint);
            BytePtr args[] = {(BytePtr)&a, (BytePtr)&b, (BytePtr)&c};
            PluginBlock block = {args, nullptr, nullptr};
            FourDPackex(1, block.fParameters, block.fData, block.fResult);
            assert(a.uValue.fArray.fNbElements == (count ? count - 1 : 0));
            for(int i = 0; i < count; ++i)
                assert(readString(PA_GetStringInArray(a, i)) == u16("fixture \xE6\x97\xA5 \xF0\x9F\x98\x80"));
            PA_ClearVariable(&a); PA_ClearVariable(&b); PA_ClearVariable(&c);
        }
        PA_Unistring input = makeString(u16("fixture"));
        BytePtr args[] = {(BytePtr)&input};
        for(int selector = 2; selector <= 12; ++selector)
        {
            sLONG_PTR number = 0;
            PA_Unistring text = makeString(u16(""));
            PA_Unistring *textPtr = &text;
            PA_Picture picture = nullptr;
            void *result = selector >= 10 ? (void *)&textPtr
                         : selector == 6 ? (void *)&picture : (void *)&number;
            PluginBlock block = {args, result, nullptr};
            FourDPackex(selector, block.fParameters, block.fData, block.fResult);
            if(selector == 4) assert(number == 1);
            if(selector == 6) { assert(picture); PA_DisposePicture(picture); }
            if(selector == 9) assert(number == 0);
            if(selector >= 10)
                assert(readString(text) == u16(selector == 11 ? "fixture \xE6\x97\xA5 \xF0\x9F\x98\x80" : "/synthetic/fixture.app"));
            PA_DisposeUnistring(&text);
        }
        assert(operations == 5);
        PA_DisposeUnistring(&input);
    }
    else if(name == "blob")
    {
        const uint8_t bytes[] = {0, 1, 255};
        C_BLOB value;
        value.setBytes(bytes, sizeof(bytes));
        PA_Handle handle = nullptr;
        BytePtr args[] = {(BytePtr)&handle};
        value.toParamAtIndex(args, 1);
        assert(handles.at(handle) == sizeof(bytes) && memcmp(*handle, bytes, sizeof(bytes)) == 0);
        C_BLOB copied;
        copied.fromParamAtIndex(args, 1);
        assert(copied.getBytesLength() == sizeof(bytes));
        PA_DisposeHandle(handle);
    }
    else std::abort();
    balanced();
    std::cout << name << " balanced creates/disposes: strings=" << stringCreates << "/" << stringDisposes
              << " handles=" << handleCreates << "/" << handleDisposes
              << " refs=" << refCreates << "/" << refDisposes
              << " pictures=" << pictureCreates << "/" << pictureDisposes << "\n";
}
int main(int argc, char **argv)
{
    assert(argc == 2);
    gCall4D = host;
    @autoreleasepool { runCase(argv[1]); }
}
