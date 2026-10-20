### Here's the process :
  here Custorem id has to be dropped cuz we can use the index number
  many datas are in double dashes, gotta fix those
  and most of then are in strings. many need to be in floot or int (before encoding i guess)
  handle nan values
 visualise
 encode

hi, its another day, i found out today that you have to handle nan value first then you can change the data type


yeah, well cannnot perform fillna without int data. these are in str data type though they are numbers. if i try to convert them i have to delete the nan value rows, which i don't want

okay, all numerical nan values are filled. now i have to figure out how i would fill the str types. do i have to encode them first?
now all the str values are filled with "mode" though i have confusion in mode was the best choise. actually don't know much about other options

after visualisation before encoding you can see in contracttype column there are "1,2,3,value,unknown" datas which dont mean anything, or i dont understand those. same problem are in plan types and maybe in more columns. and i see meny outliers in churn how  do i fix these?

i have to encode the data. numerical columns will be left as it is but i have to ordinal encode or oneHotEncode rest of the columns. (i have to know more about each encoding techniques)

next comes definign x and y then splitting them using sklearn's train-test split

