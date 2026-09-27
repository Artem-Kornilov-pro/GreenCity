// Русская типографика: неразрывный пробел после коротких слов, перед тире и
// внутри обозначений норм («СП 42.13330.2016»).
const NBSP = "\u00A0";

const SHORT_WORD = /(^|[\s«(])([А-Яа-яЁёA-Za-z]{1,2}) /g;
const BEFORE_DASH = / —/g;
const DOC_CODE = /(^|[\s«(])(СП|ГОСТ|МГСН|ППМ) (?=\d)/g;

export function typograph(text: string): string {
  // Дважды: у подряд идущих коротких слов ("и в дом") второе совпадение
  // начинается с пробела, съеденного первым.
  return text
    .replace(SHORT_WORD, `$1$2${NBSP}`)
    .replace(SHORT_WORD, `$1$2${NBSP}`)
    .replace(BEFORE_DASH, `${NBSP}—`)
    .replace(DOC_CODE, `$1$2${NBSP}`);
}
